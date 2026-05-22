import React from "react"

class TcrScaffold extends React.Component

  constructor: (props) ->
    super(props)

    {columns, rows} = @parseValue(props.rows, props.columns)

    @state =
      columns: columns
      rows: rows

    @on_preparation_change = @on_preparation_change.bind @
    @on_scaffold_change     = @on_scaffold_change.bind @
    @add_scaffold_item      = @add_scaffold_item.bind @
    @remove_scaffold_item   = @remove_scaffold_item.bind @
    @save                   = @save.bind @

  parseValue: (rows, columns) ->
    cols = columns or []
    rs   = (rows or []).map (row) ->
      r = Object.assign {}, row
      r.__preparation__ = if r.__preparation__? then Boolean(r.__preparation__) else false
      if not r.__scaffolds__? or not Array.isArray(r.__scaffolds__)
        scaffold1 = r.__scaffold1__ or ""
        scaffold2 = r.__scaffold2__ or ""
        if scaffold1 or scaffold2
          r.__scaffolds__ = [{scaffold: scaffold1, quantity: scaffold2}]
        else
          r.__scaffolds__ = [{scaffold: "", quantity: ""}]
      r
    {columns: cols, rows: rs}

  componentDidUpdate: (prevProps) ->
    prev_rows = prevProps.rows or []
    curr_rows = @props.rows   or []
    return if JSON.stringify(prev_rows) is JSON.stringify(curr_rows)
    {columns, rows} = @parseValue(@props.rows, @props.columns)
    @setState {columns, rows}

  on_preparation_change: (row_idx, event) ->
    checked = event.target.checked
    rows = @state.rows.map (row, i) ->
      if i is row_idx
        r = Object.assign {}, row
        r.__preparation__ = checked
        r
      else
        row
    @setState {rows}, => @save()

  on_scaffold_change: (row_idx, item_idx, field, event) ->
    val = event.target.value
    rows = @state.rows.map (row, i) ->
      if i is row_idx
        r = Object.assign {}, row
        scaffolds = (r.__scaffolds__ or []).map (item, j) ->
          if j is item_idx
            s = Object.assign {}, item
            s[field] = val
            s
          else
            item
        r.__scaffolds__ = scaffolds
        r
      else
        row
    @setState {rows}, => @save()

  add_scaffold_item: (row_idx) ->
    rows = @state.rows.map (row, i) ->
      if i is row_idx
        r = Object.assign {}, row
        r.__scaffolds__ = (r.__scaffolds__ or []).concat [{scaffold: "", quantity: ""}]
        r
      else
        row
    @setState {rows}, => @save()

  remove_scaffold_item: (row_idx, item_idx) ->
    rows = @state.rows.map (row, i) ->
      if i is row_idx
        r = Object.assign {}, row
        scaffolds = (r.__scaffolds__ or []).filter (_, j) -> j isnt item_idx
        r.__scaffolds__ = if scaffolds.length > 0 then scaffolds else [{scaffold: "", quantity: ""}]
        r
      else
        row
    @setState {rows}, => @save()

  save: ->
    uid        = @props.uid
    column_key = @props.column_key
    payload = JSON.stringify
      columns: @state.columns
      rows:    @state.rows
    if @props.update_editable_field?
      @props.update_editable_field uid, column_key, payload, @props.item
    if @props.save_editable_field?
      @props.save_editable_field uid, column_key, payload, @props.item

  render: ->
    {columns, rows} = @state
    disabled = @props.disabled or false

    if not columns or columns.length is 0
      return <span style={{color: "#999", fontSize: "12px"}}>{"暂无骨架制备序列"}</span>

    if rows.length is 0
      return <span style={{color: "#999", fontSize: "12px"}}>{"暂无已选TCR序列"}</span>

    <div className="lp-tcr-scaffold" style={{width: "100%", overflowX: "auto"}}>
      <table className="table table-bordered table-condensed table-striped"
             style={{fontSize: "12px", marginBottom: 0, width: "100%", whiteSpace: "nowrap"}}>
        <thead>
          <tr>
            <th style={{width:"160px"}}>{"TCR序列代码"}</th>
            <th style={{width: "60px", textAlign: "center"}}>{"优先级"}</th>
            <th style={{width: "300px", minWidth: "300px"}}>{"骨架 / 产量"}</th>
            {columns.map (col, i) =>
              <th key={i} style={{whiteSpace: "nowrap"}}>{col}</th>
            }
          </tr>
        </thead>
        <tbody>
          {rows.map (row, row_idx) =>
            is_prep   = Boolean(row.__preparation__)
            scaffolds = row.__scaffolds__ or [{scaffold: "", quantity: ""}]
            rowBg     = if is_prep then "#edf6ff" else ""

            <tr key={row_idx} style={{background: rowBg, verticalAlign: "middle"}}>

              <td style={{verticalAlign:"middle",color:"#555",fontFamily:"monospace"}}>
                {row.__tcr_code__ or ""}
              </td>

              <td style={{color: "#337ab7", fontWeight: "bold", textAlign: "center", verticalAlign: "middle"}}>
                {row.__priority__ or ""}
              </td>

              <td style={{padding: "4px 6px", verticalAlign: "middle"}}>
                {scaffolds.map (item, item_idx) =>
                  isLast = item_idx is scaffolds.length - 1
                  <div key={item_idx}
                       style={{
                         display: "flex",
                         alignItems: "center",
                         marginBottom: if isLast then "0" else "3px"
                       }}>
                    <input
                      type="text"
                      className="form-control input-sm"
                      placeholder="骨架"
                      value={item.scaffold or ""}
                      disabled={disabled}
                      onChange={(e) => @on_scaffold_change(row_idx, item_idx, "scaffold", e)}
                      style={{width: "110px", marginRight: "4px", height: "24px", padding: "2px 6px"}} />
                    <input
                      type="text"
                      className="form-control input-sm"
                      placeholder="产量"
                      value={item.quantity or ""}
                      disabled={disabled}
                      onChange={(e) => @on_scaffold_change(row_idx, item_idx, "quantity", e)}
                      style={{width: "90px", marginRight: "4px", height: "24px", padding: "2px 6px"}} />
                    {if not disabled
                      <span style={{display: "flex", alignItems: "center", gap: "2px"}}>
                        {if scaffolds.length > 1
                          <button
                            type="button"
                            className="btn btn-danger"
                            onClick={=> @remove_scaffold_item(row_idx, item_idx)}
                            style={{
                              padding: "0px 6px",
                              height: "24px",
                              lineHeight: "22px",
                              fontSize: "14px",
                              fontWeight: "bold",
                              borderRadius: "3px"
                            }}>
                            {"−"}
                          </button>
                        }
                        {if isLast
                          <button
                            type="button"
                            className="btn btn-default"
                            onClick={=> @add_scaffold_item(row_idx)}
                            style={{
                              padding: "0px 6px",
                              height: "24px",
                              lineHeight: "22px",
                              fontSize: "14px",
                              fontWeight: "bold",
                              borderRadius: "3px"
                            }}>
                            {"+"}
                          </button>
                        }
                      </span>
                    }
                  </div>
                }
              </td>

              {columns.map (col, ci) =>
                <td key={ci} style={{verticalAlign: "middle"}}>
                  {row[col] or ""}
                </td>
              }
            </tr>
          }
        </tbody>
      </table>
    </div>

export default TcrScaffold
