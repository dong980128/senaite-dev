import React from "react"

class TcrPreparation extends React.Component

  constructor: (props) ->
    super(props)

    {columns, rows} = @parseValue(props.rows, props.columns)

    @state =
      columns: columns
      rows: rows

    @on_preparation_change = @on_preparation_change.bind @
    @save                  = @save.bind @

  parseValue: (rows, columns) ->
    cols = columns or []
    rs   = (rows or []).map (row) ->
      r = Object.assign {}, row
      r.__preparation__ = if r.__preparation__? then Boolean(r.__preparation__) else false
      r
    {columns: cols, rows: rs}

  componentDidUpdate: (prevProps) ->
    prev_rows = prevProps.rows or []
    curr_rows = @props.rows   or []
    return if JSON.stringify(prev_rows) is JSON.stringify(curr_rows)
    {columns, rows} = @parseValue(@props.rows, @props.columns)
    @setState {columns, rows}

  on_preparation_change: (idx, event) ->
    checked = event.target.checked
    rows = @state.rows.map (row, i) ->
      if i is idx
        r = Object.assign {}, row
        r.__preparation__ = checked
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
      return <span style={{color: "#999", fontSize: "12px"}}>{"暂无制备序列"}</span>

    if rows.length is 0
      return <span style={{color: "#999", fontSize: "12px"}}>{"暂无已选TCR序列"}</span>

    <div className="lp-tcr-preparation"
         style={{width: "100%", overflowX: "auto"}}>
      <table className="table table-bordered table-condensed table-striped"
             style={{
               fontSize: "12px",
               marginBottom: 0,
               width: "100%",
               whiteSpace: "nowrap"
             }}>
        <thead>
          <tr>
            <th style={{width: "60px", textAlign: "center"}}>
              {"是否制备"}

            </th>
            <th style={{width:"160px"}}>{"TCR序列代码"}</th>
            <th style={{width: "70px"}}>
              {"优先级"}
            </th>
            {columns.map (col, i) =>
              <th key={i} style={{whiteSpace: "nowrap"}}>
                {col}
              </th>
            }
          </tr>
        </thead>
        <tbody>
          {rows.map (row, idx) =>
            is_prep = Boolean(row.__preparation__)
            <tr key={idx}
                style={{background: if is_prep then "#edf6ff" else ""}}>
              <td style={{textAlign: "center", verticalAlign: "middle"}}>
                <input
                  type="checkbox"
                  checked={is_prep}
                  disabled={disabled}
                  onChange={(e) => @on_preparation_change(idx, e)} />
              </td>

              <td style={{verticalAlign:"middle", color:"#555",fontFamily:"monospace"}}>
                {row.__tcr_code__ or ""}
              </td>
              <td style={{
                verticalAlign: "middle",
                color: "#337ab7",
                fontWeight: "bold",
                textAlign: "center"
              }}>
                {row.__priority__ or ""}
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

export default TcrPreparation