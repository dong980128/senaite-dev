import React from "react"

class TcrPlasmid extends React.Component

  constructor: (props) ->
    super(props)

    {columns, rows} = @parseValue(props.rows, props.columns)

    @state =
      columns: columns
      rows: rows

    @on_field_change = @on_field_change.bind @
    @save            = @save.bind @

  parseValue: (rows, columns) ->
    cols = columns or []
    rs   = (rows or []).map (row) ->
      r = Object.assign {}, row
      r.__plasmid_no__   = r.__plasmid_no__   or ""
      r.__plasmid_name__ = r.__plasmid_name__ or ""
      r
    {columns: cols, rows: rs}

  componentDidUpdate: (prevProps) ->
    prev_rows = prevProps.rows or []
    curr_rows = @props.rows   or []
    return if JSON.stringify(prev_rows) is JSON.stringify(curr_rows)
    {columns, rows} = @parseValue(@props.rows, @props.columns)
    @setState {columns, rows}

  on_field_change: (row_idx, field, event) ->
    val = event.target.value
    rows = @state.rows.map (row, i) ->
      if i is row_idx
        r = Object.assign {}, row
        r[field] = val
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

    if rows.length is 0
      return <span style={{color: "#999", fontSize: "12px"}}>{"暂无质粒数据"}</span>

    inputStyle =
      width: "120px"
      height: "24px"
      padding: "2px 6px"
      fontSize: "12px"
      color: "#555"

    placeholderColor = "#bbb"

    <div className="lp-tcr-plasmid" style={{width: "100%", overflowX: "auto"}}>
      <style dangerouslySetInnerHTML={{__html: "
        .lp-tcr-plasmid input::placeholder { color: #bbb; font-size: 11px; }
      "}} />
      <table className="table table-bordered table-condensed table-striped"
             style={{fontSize: "12px", marginBottom: 0, width: "100%", whiteSpace: "nowrap"}}>
        <thead>
          <tr>
            <th style={{width: "160px", fontSize: "12px"}}>{"TCR序列代码"}</th>
            <th style={{width: "60px", textAlign: "center"}}>{"优先级"}</th>
            <th style={{width: "100px"}}>{"骨架"}</th>
            <th style={{width: "80px"}}>{"产量"}</th>
            <th style={{width: "140px"}}>{"质粒编号"}</th>
            <th style={{width: "140px"}}>{"质粒名称"}</th>
            {columns.map (col, i) =>
              <th key={i} style={{whiteSpace: "nowrap"}}>{col}</th>
            }
          </tr>
        </thead>
        <tbody>
          {rows.map (row, row_idx) =>
            <tr key={row_idx}>
              <td style={{verticalAlign: "middle", fontSize: "12px", color: "#333"}}>
                {row.__tcr_code__ or ""}
              </td>
              <td style={{verticalAlign: "middle", color: "#337ab7", fontWeight: "bold", textAlign: "center"}}>
                {row.__priority__ or ""}
              </td>
              <td style={{verticalAlign: "middle"}}>
                {row.__scaffold__ or ""}
              </td>
              <td style={{verticalAlign: "middle"}}>
                {row.__quantity__ or ""}
              </td>
              <td style={{verticalAlign: "middle", padding: "2px 4px"}}>
                <input
                  type="text"
                  className="form-control input-sm"
                  placeholder="质粒编号"
                  value={row.__plasmid_no__ or ""}
                  disabled={disabled}
                  onChange={(e) => @on_field_change(row_idx, "__plasmid_no__", e)}
                  style={inputStyle} />
              </td>
              <td style={{verticalAlign: "middle", padding: "2px 4px"}}>
                <input
                  type="text"
                  className="form-control input-sm"
                  placeholder="质粒名称"
                  value={row.__plasmid_name__ or ""}
                  disabled={disabled}
                  onChange={(e) => @on_field_change(row_idx, "__plasmid_name__", e)}
                  style={inputStyle} />
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

export default TcrPlasmid
