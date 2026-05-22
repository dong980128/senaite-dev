import React from "react"

class PosNegWithNote extends React.Component

  constructor: (props) ->
    super(props)

    # 从后端原始字符串解析 {status, value}
    {status, val} = @parseValue(props.value or props.defaultValue or "")

    @state =
      status: status   # 阳性 / 阴性 / 未检测 / ""
      value:  val      # 只有阳性时才有值

    @on_status_change = @on_status_change.bind @
    @on_status_blur   = @on_status_blur.bind @
    @on_value_change  = @on_value_change.bind @
    @on_value_blur    = @on_value_blur.bind @

  # -------------------------
  # 解析后端值
  # JSON: {"status":"阳性","value":"10"}
  # 兼容旧格式: "阳性|10"
  # -------------------------
  parseValue: (raw) ->
    status = ""
    val    = ""

    return {status, val} unless raw?
    raw = String(raw)

    # 先试 JSON
    try
      obj = JSON.parse(raw)
      if obj? and typeof obj is "object"
        status = obj.status or obj.sel or ""
        val    = obj.value  or obj.val or ""
        return {status, val}
    catch e
    # 兼容 "阳性|10"
    if "|" in raw
      [s, v] = raw.split "|", 2
      status = s or ""
      val    = v or ""
    else
      status = raw or ""
      val    = ""

    {status, val}

  # -------------------------
  # 组装 payload：JSON 字符串
  # 非阳性时 value 统一清空
  # -------------------------
  makePayload: (status = @state.status, value = @state.value) ->
    s = status or ""
    v = value  or ""

    unless s in ["阳性", "positive", "A"]
      v = ""

    JSON.stringify
      status: s
      value: v

  componentDidUpdate: (prevProps) ->
    prevRaw = prevProps.value or prevProps.defaultValue or ""
    raw     = @props.value or @props.defaultValue or ""
    return if raw is prevRaw

    {status, val} = @parseValue(raw)
    @setState
      status: status
      value:  val

  build_options: ->
    opts_src = @props.options or []
    options  = []

    if opts_src.length is 0
      opts_src = [
        {ResultValue: "阳性",   ResultText: "阳性",   ResultDescription: "阳性"}
        {ResultValue: "阴性",   ResultText: "阴性",   ResultDescription: "阴性"}
        {ResultValue: "未检测", ResultText: "未检测", ResultDescription: "未检测"}
      ]

    for opt, idx in opts_src
      if typeof opt is "string"
        val  = opt
        text = opt
        desc = opt
      else
        val  = opt.ResultValue ? opt.value ? opt.key
        text = opt.ResultText ? opt.text ? opt.title ? val
        desc = opt.ResultDescription ? text

      options.push(
        <option key={idx}
                title={desc}
                value={val}>
          {text}
        </option>
      )

    # 空选项
    options.unshift <option key="_empty" value=""></option>

    options

  _getUidAndNameFromEl: (el) ->
    uid  = el.getAttribute("uid") or @props.uid
    name = el.getAttribute("column_key") or @props.column_key or @props.name
    {uid, name}

  on_status_change: (event) ->
    el = event.currentTarget
    {uid, name} = @_getUidAndNameFromEl(el)

    status = el.value
    # 非阳性时清空数值
    value  = if status in ["阳性", "positive", "A"] then @state.value else ""

    @setState
      status: status
      value:  value

    payload = @makePayload(status, value)
    console.debug "PosNegWithNote::on_status_change =>", uid, name, payload

    if @props.update_editable_field?
      @props.update_editable_field uid, name, payload, @props.item

  on_status_blur: (event) ->
    el = event.currentTarget
    {uid, name} = @_getUidAndNameFromEl(el)

    payload = @makePayload()
    console.debug "PosNegWithNote::on_status_blur =>", uid, name, payload

    if @props.save_editable_field?
      @props.save_editable_field uid, name, payload, @props.item

  on_value_change: (event) ->
    val = event.currentTarget.value
    @setState value: val

  on_value_blur: (event) ->
    el = event.currentTarget
    {uid, name} = @_getUidAndNameFromEl(el)

    val    = el.value
    status = @state.status

    @setState value: val

    payload = @makePayload(status, val)
    console.debug "PosNegWithNote::on_value_blur =>", uid, name, payload

    if @props.update_editable_field?
      @props.update_editable_field uid, name, payload, @props.item

    if @props.save_editable_field?
      @props.save_editable_field uid, name, payload, @props.item

  render: ->
    payload = @makePayload()
    show_value_input = @state.status in ["阳性", "positive", "A"]

    <span className={@props.field_css or "form-group"}>
      <input
        type="hidden"
        name={@props.name}
        value={payload}
        form={@props.formId} />

      <select
        key={@props.name}
        uid={@props.uid}
        name={@props.name}
        column_key={@props.column_key}
        value={@state.status or ""}
        title={@props.help or @props.title}
        disabled={@props.disabled}
        onBlur={@on_status_blur}
        onChange={@on_status_change}
        required={@props.required}
        className={@props.className}
        tabIndex={@props.tabIndex}
        {...@props.attrs}>
        {@build_options()}
      </select>

      { show_value_input and
        <input
          type="text"
          uid={@props.uid}
          column_key={@props.column_key}
          value={@state.value}
          onChange={@on_value_change}
          onBlur={@on_value_blur}
          style={{marginLeft: "4px", width: "70px"}} />
      }
    </span>

export default PosNegWithNote
