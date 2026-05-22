import React from "react"

class TcrSelector extends React.Component

  constructor: (props) ->
    super(props)

    # 解析后端传来的数据
    {columns, rows} = @parseValue(props.rows, props.columns)

    @state =
      columns: columns        # 列名列表
      rows: rows              # 数据行列表
      sort_key: null          # 当前排序列
      sort_asc: true          # 升序/降序

    @on_check_change    = @on_check_change.bind @
    @on_priority_change = @on_priority_change.bind @
    @on_priority_blur   = @on_priority_blur.bind @
    @on_sort            = @on_sort.bind @
    @save               = @save.bind @

  parseValue: (rows, columns) ->
    cols = columns or []
    rs   = rows    or []

    # 确保每行都有 __checked__ 和 __priority__
    rs = rs.map (row) ->
      r = Object.assign {}, row
      r.__checked__  = if r.__checked__? then Boolean(r.__checked__) else false
      r.__priority__ = r.__priority__ or ""
      r

    {columns: cols, rows: rs}

  componentDidUpdate: (prevProps) ->
    prev_rows = prevProps.rows or []
    curr_rows = @props.rows   or []
    return if JSON.stringify(prev_rows) is JSON.stringify(curr_rows)
    {columns, rows} = @parseValue(@props.rows, @props.columns)
    @setState {columns, rows}

  # 勾选/取消勾选某行
  on_check_change: (idx, event) ->
    checked = event.target.checked
    rows = @state.rows.map (row, i) ->
      if i is idx
        r = Object.assign {}, row
        r.__checked__ = checked
        if not checked
          r.__priority__ = ""
        r
      else
        row
    @setState {rows}, => @save()

  # 优先级输入
  on_priority_change: (idx, event) ->
    val = event.target.value
    rows = @state.rows.map (row, i) ->
      if i is idx
        r = Object.assign {}, row
        r.__priority__ = val
        r
      else
        row
    @setState {rows}

  on_priority_blur: (idx, event) ->
    @save()

  # 列排序
  on_sort: (col_key) ->
    {sort_key, sort_asc, rows} = @state

    new_asc = if sort_key is col_key then not sort_asc else true

    sorted = rows.slice().sort (a, b) ->
      va = a[col_key] or ""
      vb = b[col_key] or ""
      # 尝试数字排序
      na = parseFloat(va)
      nb = parseFloat(vb)
      if not isNaN(na) and not isNaN(nb)
        result = na - nb
      else
        result = if va < vb then -1 else if va > vb then 1 else 0
      if new_asc then result else -result

    @setState
      rows: sorted
      sort_key: col_key
      sort_asc: new_asc
    , => @save()

  # 保存：把当前 rows 序列化成 JSON 写回 InterimField
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

  render_sort_icon: (col_key) ->
    {sort_key, sort_asc} = @state
    if sort_key isnt col_key
      return <span style={{color: "#ccc", marginLeft: "4px"}}>{"⇅"}</span>
    if sort_asc
      return <span style={{color: "#337ab7", marginLeft: "4px"}}>{"↑"}</span>
    return <span style={{color: "#337ab7", marginLeft: "4px"}}>{"↓"}</span>

  render: ->
    {columns, rows} = @state
    disabled = @props.disabled or false

    if not columns or columns.length is 0
      return <span style={{color: "#999", fontSize: "12px"}}>{"暂无TCR数据"}</span>
    <div className="lp-tcr-selector"
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
            <th style={{width: "45px", textAlign: "center", whiteSpace: "nowrap"}}>
              {"选择"}
            </th>
            <th style={{width: "70px", whiteSpace: "nowrap"}}>
              {"优先级"}
            </th>
            {columns.map (col, i) =>
              <th key={i}
                  style={{whiteSpace: "nowrap", cursor: "pointer"}}
                  onClick={=> @on_sort(col)}>
                {col}
                {@render_sort_icon(col)}
              </th>
            }
          </tr>
        </thead>

        <tbody>
          {rows.map (row, idx) =>
            is_checked = Boolean(row.__checked__)
            <tr key={idx}
                style={{background: if is_checked then "#edf6ff" else ""}}>

              <td style={{textAlign: "center", verticalAlign: "middle"}}>
                <input
                  type="checkbox"
                  checked={is_checked}
                  disabled={disabled}
                  onChange={(e) => @on_check_change(idx, e)} />
              </td>
                    
              <td style={{verticalAlign: "middle"}}>
                {if is_checked and not disabled
                  <input
                    type="text"
                    value={row.__priority__ or ""}
                    onChange={(e) => @on_priority_change(idx, e)}
                    onBlur={(e)   => @on_priority_blur(idx, e)}
                    style={{width: "60px", fontSize: "12px", padding: "1px 4px"}}
                    placeholder={"优先级"} />
                else
                  <span style={{color: if is_checked then "#333" else "#ccc"}}>
                    {row.__priority__ or ""}
                  </span>
                }
              </td>
              {columns.map (col, ci) =>
                <td key={ci} style={{verticalAlign: "middle", whiteSpace: "nowrap"}}>
                  {row[col] or ""}
                </td>
              }

            </tr>
          }
        </tbody>

      </table>
    </div>

export default TcrSelector