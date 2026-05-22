import React from "react"

import Checkbox from "./Checkbox.coffee"
import HiddenField from "./HiddenField.coffee"
import MultiChoice from "./MultiChoice.coffee"
import MultiSelect from "./MultiSelect.coffee"
import MultiValue from "./MultiValue.coffee"
import NumericField from "./NumericField.coffee"
import CalculatedField from "./CalculatedField.coffee"
import ReadonlyField from "./ReadonlyField.coffee"
import Select from "./Select.coffee"
import StringField from "./StringField.coffee"
import TextField from "./TextField.coffee"
import FractionField from "./FractionField.coffee"
import DateTime from "./DateTime.coffee"
import TieredMultiValue from "./TieredMultiValue.coffee"
import PosNegWithNote from "./PosNegWithNote.coffee"
import FileField from "./FileField.coffee"
import TcrSelector from "./TcrSelector.coffee"
import TcrPreparation from "./TcrPreparation.coffee"
import Tick from "./Tick.coffee"
import TcrScaffold from "./TcrScaffold.coffee"

class TableCell extends React.Component

  constructor: (props) ->
    super(props)

    # Zope Publisher Converter Argument Mapping
    @ZPUBLISHER_CONVERTER =
      "boolean": ":record:ignore_empty"
      "tick":":record:ignore_empty"
      "select": ":records"
      "choices": ":records"
      "multiselect": ":list"
      "multichoice": ":list"
      "multivalue": ":list"
      "tiered_multivalue": ":list"
      "multivalue:tiered": ":list"
      "numeric": ":records"
      "fraction": ":records"
      "string": ":records"
      "text": ":records"
      "datetime": ":records"
      "file": ":records"
      "tcr_selector": ":records"
      "tcr_preparation": ":records"
      "tcr_scaffold": ":records"
      "tcr_plasmid": ":records"
      "posneg_with_note": ":records"
      "readonly": ""
      "default": ":records"

  get_column: -> @props.column
  get_item:   -> @props.item
  get_column_key: -> @props.column_key

  # -----------------------------
  # before 内容渲染
  # -----------------------------
  render_before_content: (props = {}) ->
    column_key = @get_column_key()
    item = @get_item()
    return unless item
    before = item.before
    if not before? or column_key not of before
      return null

    # 支持渲染 React 组件
    before_components = item.before_components or {}

    return (
      <span key={column_key + "_before"} className="before-item">
        {before_components[column_key]}
        <span
          dangerouslySetInnerHTML={{__html: before[column_key]}}
          {...props}>
        </span>
      </span>
    )

  # 有的行有 after，这里给个最简单的实现，避免调用报错
  render_after_content: (props = {}) ->
    column_key = @get_column_key()
    item = @get_item()
    return unless item
    after = item.after
    if not after? or column_key not of after
      return null

    after_components = item.after_components or {}

    return (
      <span key={column_key + "_after"} className="after-item">
        {after_components[column_key]}
        <span
          dangerouslySetInnerHTML={{__html: after[column_key]}}
          {...props}>
        </span>
      </span>
    )

  is_edit_allowed: ->
    column_key = @get_column_key()
    item = @get_item()

    # the global allow_edit overrides all row specific settings
    if not @props.allow_edit
      return no

    # check if the field is listed in the item's allow_edit list
    if column_key in item.allow_edit
      return yes

    return no

  is_disabled: ->
    item = @get_item()
    disabled = item.disabled
    if disabled in [yes, no]
      return disabled

    return no unless disabled?

    column_key = @get_column_key()
    return column_key in disabled

  is_required: ->
    column_key = @get_column_key()
    item = @get_item()
    required_fields = item.required or []
    required = column_key in required_fields
    selected = @props.selected
    return required and selected

  get_name: ->
    uid = @get_uid()
    column_key = @get_column_key()
    "#{column_key}.#{uid}"

  get_uid: ->
    item = @get_item()
    item.uid

  is_selected: ->
    item = @get_item()
    item.uid in @props.selected_uids

  get_value: ->
    column_key = @get_column_key()
    item = @get_item()
    value = item[column_key]

    # check if the field is an interim
    interims = @get_interimfields()
    if interims.hasOwnProperty column_key
      # {value: "", keyword: "", formatted_value: "", unit: "", title: ""}
      value = interims[column_key].value or ""

    if value is null
      value = ""

    value

  ###
  Returns the size for the folderitem or interim field
  ###
  get_size: ->
    default_size = 5
    types_size =
      "string": 30
      "text": 30

    item = @get_item()
    column_key = @get_column_key()

    if @is_interimfield()
      interim = item[column_key]
      if interim and interim.hasOwnProperty "size"
        return interim.size

    sizes = item.size or {}
    if column_key of sizes
      return sizes[column_key]

    column = @props.column or {}
    if "size" of column
      return column.size

    type = @get_type()
    if type of types_size
      return types_size[type]

    default_size

  ###*
  # Create a mapping of interim keyword -> interim field
  ###
  get_interimfields: ->
    item = @get_item()
    interims = item.interimfields or []
    mapping = {}
    interims.map (entry) ->
      mapping[entry.keyword] = entry
    mapping

  is_interimfield: ->
    column_key = @get_column_key()
    interims = @get_interimfields()
    interims.hasOwnProperty column_key

  get_choices: ->
    item = @get_item()
    item.choices or {}

  is_result_column: ->
    column_key = @get_column_key()
    if column_key == "Result" then yes else no

  get_formatted_value: ->
    column_key = @get_column_key()
    item = @get_item()
    formatted_value = item.replace[column_key] or @get_value()
    if @is_result_column()
      console.debug "[TableCell] format posneg:", column_key, "raw=", formatted_value
      formatted_value = item.formatted_result or formatted_value

    if @is_posneg_with_note()
      formatted_value = @format_posneg_with_note(formatted_value)

    formatted_value

  get_type: ->
    column_key = @get_column_key()
    item = @get_item()
    editable = @is_edit_allowed()
    unless editable
      if @is_interimfield()
        interim = item[column_key]
        if interim?.result_type == "tick"
          return "tick"
      return "readonly"
    resultfield = @is_result_column()

    unless editable
      return "readonly"

    if resultfield and item.calculation
      return "calculated"

    if resultfield and item.result_type
      return item.result_type

    if @is_interimfield()
      interim = item[column_key]
      if interim?.result_type?
        return interim.result_type

    column = @props.column or {}
    if "type" of column
      return column["type"]

    value = @get_value()
    if typeof (value) == "boolean"
      return "boolean"

    choices = @get_choices()
    if column_key of choices
      default_type = "select"
      if resultfield
        return item.result_type or default_type
      if @is_interimfield()
        interim = item[column_key]
        if interim
          return interim.result_type or default_type
      return default_type

    if @is_interimfield()
      default_type = "interim"
      interim = item[column_key]
      if interim
        return interim.result_type or default_type
      return default_type

    "numeric"

  is_posneg_with_note: ->
    column_key = @get_column_key()
    item = @get_item()

    # 主 Result 列
    if @is_result_column()
      return item.result_type is "posneg_with_note"

    # interim 列（比如 result_qpc_MAGE-A4 这些）
    interims = @get_interimfields()
    if interims.hasOwnProperty column_key
      interim = interims[column_key]
      return interim?.result_type is "posneg_with_note"

    false

  format_posneg_with_note: (raw) ->
    return "" unless raw?
    str = String(raw)

    status = ""
    val    = ""

    # 先试 JSON: {"status":"A","value":"10"}
    try
      obj = JSON.parse(str)
      if obj? and typeof obj is "object"
        status = obj.status or ""
        val    = obj.value  or ""
      else
        status = str
    catch e
      # 兼容 "A|10" 这种旧格式
      if "|" in str
        [s, v] = str.split "|", 2
        status = s or ""
        val    = v or ""
      else
        status = str

    status = String(status or "").toUpperCase()

    # ==== 从 choices 里取中文标签 ====
    column_key = @get_column_key()
    item = @get_item()
    label = status
    mapping = {}

    if item.choices? and column_key of item.choices
      raw_choices = item.choices[column_key]

      if typeof raw_choices is "string"
        # "A:阳性|B:阴性|C:未检测"
        for part in raw_choices.split "|"
          if ":" in part
            [k, txt] = part.split ":", 2
            mapping[k] = txt
      else if Array.isArray(raw_choices)
        # [{ResultValue, ResultText, ...}, ...]
        raw_choices.forEach (opt) ->
          v = opt.ResultValue or opt.value or opt.key
          t = opt.ResultText or opt.text or opt.title or v
          mapping[v] = t

      if status of mapping
        label = mapping[status]

    # ==== 显示规则 ====
    # 阳性 → 显示数值（没数值就退回 label）
    if status in ["A", "阳性", "POSITIVE"]
      return val or label

    # 阴性 / 未检测 → 显示对应文字
    if status in ["B", "阴性", "NEGATIVE"]
      return mapping["B"] or "阴性"

    if status in ["C", "未检测"]
      return mapping["C"] or "未检测"

    if val then "#{label}(#{val})" else label

  # ===== factory methods =====

  create_readonly_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    css_class = props.css_class or "readonly"

    return (
      <ReadonlyField
        key={name}
        uid={uid}
        name={name}
        value={value}
        formatted_value={formatted_value}
        className={css_class}
        {...props}
      />
    )

  create_calculated_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    title = props.title or @props.column.title or column_key

    column = props.column or @get_column()
    item.help ?= {}
    help = props.help or item.help[column_key] or column.help

    selected = props.selected or @is_selected()
    required = props.required or @is_required()
    size = props.size or @get_size()
    css_class = props.css_class or "form-control form-control-sm calculated"
    if required then css_class += " required"

    return (
      <CalculatedField
        key={name + formatted_value}
        uid={uid}
        item={item}
        name={name}
        value={value}
        column_key={column_key}
        title={title}
        help={help}
        formatted_value={formatted_value}
        placeholder={title}
        selected={selected}
        required={required}
        className={css_class}
        update_editable_field={@props.update_editable_field}
        save_editable_field={@props.save_editable_field}
        size={size}
        {...props}
      />
    )

  create_hidden_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    uid = props.uid or @get_uid()

    return (
      <HiddenField
        key={name + "_hidden"}
        uid={uid}
        name={name}
        value={value}
        column_key={column_key}
        {...props}
      />
    )

  create_numeric_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    title = props.title or @props.column.title or column_key

    column = props.column or @get_column()
    item.help ?= {}
    help = props.help or item.help[column_key] or column.help

    converter = @ZPUBLISHER_CONVERTER["numeric"]
    fieldname = name + converter

    selected = props.selected or @is_selected()
    disabled = props.disabled or @is_disabled()
    required = props.required or @is_required()
    size = props.size or @get_size()
    css_class = props.css_class or "form-control form-control-sm"
    if required then css_class += " required"

    return (
      <NumericField
        key={name + formatted_value}
        uid={uid}
        item={item}
        name={fieldname}
        defaultValue={value}
        column_key={column_key}
        title={title}
        help={help}
        formatted_value={formatted_value}
        placeholder={title}
        selected={selected}
        disabled={disabled}
        required={required}
        className={css_class}
        update_editable_field={@props.update_editable_field}
        save_editable_field={@props.save_editable_field}
        tabIndex={@props.tabIndex}
        size={size}
        {...props}
      />
    )

  create_string_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    title = props.title or @props.column.title or column_key

    column = props.column or @get_column()
    item.help ?= {}
    help = props.help or item.help[column_key] or column.help

    converter = @ZPUBLISHER_CONVERTER["string"]
    fieldname = name + converter

    selected = props.selected or @is_selected()
    disabled = props.disabled or @is_disabled()
    required = props.required or @is_required()
    size = props.size or @get_size()
    css_class = props.css_class or "form-control form-control-sm"
    if required then css_class += " required"

    return (
      <StringField
        key={name + formatted_value}
        uid={uid}
        item={item}
        name={fieldname}
        defaultValue={value}
        column_key={column_key}
        title={title}
        help={help}
        formatted_value={formatted_value}
        placeholder={title}
        selected={selected}
        disabled={disabled}
        required={required}
        className={css_class}
        update_editable_field={@props.update_editable_field}
        save_editable_field={@props.save_editable_field}
        tabIndex={@props.tabIndex}
        size={size}
        {...props}
      />
    )

  create_text_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    title = props.title or @props.column.title or column_key

    column = props.column or @get_column()
    item.help ?= {}
    help = props.help or item.help[column_key] or column.help

    converter = @ZPUBLISHER_CONVERTER["text"]
    fieldname = name + converter

    selected = props.selected or @is_selected()
    disabled = props.disabled or @is_disabled()
    required = props.required or @is_required()
    size = props.size or @get_size()
    css_class = props.css_class or "form-control form-control-sm"
    if required then css_class += " required"

    return (
      <TextField
        key={name + formatted_value}
        uid={uid}
        item={item}
        name={fieldname}
        defaultValue={value}
        column_key={column_key}
        title={title}
        help={help}
        formatted_value={formatted_value}
        placeholder={title}
        selected={selected}
        disabled={disabled}
        required={required}
        className={css_class}
        update_editable_field={@props.update_editable_field}
        save_editable_field={@props.save_editable_field}
        tabIndex={@props.tabIndex}
        size={size}
        {...props}
      />
    )

  create_fraction_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    title = props.title or @props.column.title or column_key

    column = props.column or @get_column()
    item.help ?= {}
    help = props.help or item.help[column_key] or column.help

    converter = @ZPUBLISHER_CONVERTER["fraction"]
    fieldname = name + converter

    selected = props.selected or @is_selected()
    disabled = props.disabled or @is_disabled()
    required = props.required or @is_required()
    size = props.size or @get_size()
    css_class = props.css_class or "form-control form-control-sm"
    if required then css_class += " required"

    return (
      <FractionField
        key={name + formatted_value}
        uid={uid}
        item={item}
        name={fieldname}
        defaultValue={value}
        column_key={column_key}
        title={title}
        help={help}
        formatted_value={formatted_value}
        placeholder={title}
        selected={selected}
        disabled={disabled}
        required={required}
        className={css_class}
        update_editable_field={@props.update_editable_field}
        save_editable_field={@props.save_editable_field}
        tabIndex={@props.tabIndex}
        size={size}
        {...props}
      />
    )

  create_datetime_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    type = props.type or @get_type()
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    title = props.title or @props.column.title or column_key

    column = props.column or @get_column()
    item.help ?= {}
    help = props.help or item.help[column_key] or column.help

    result_type = "date"
    converter = @ZPUBLISHER_CONVERTER["string"]
    fieldname = name + converter

    selected = props.selected or @is_selected()
    disabled = props.disabled or @is_disabled()
    required = props.required or @is_required()
    size = props.size or @get_size()
    css_class = props.css_class or "form-control form-control-sm"
    if required then css_class += " required"

    min_date = null
    max_date = null
    min_time = null
    max_time = null

    min = column.min or null
    max = column.max or null
    if min
      [min_date, min_time] = min.split(" ")
    if max
      [max_date, max_time] = max.split(" ")

    return (
      <DateTime
        key={name + formatted_value}
        uid={uid}
        item={item}
        name={fieldname}
        defaultValue={value}
        column_key={column_key}
        title={title}
        help={help}
        formatted_value={formatted_value}
        placeholder={title}
        selected={selected}
        disabled={disabled}
        required={required}
        className={css_class}
        results_type={result_type}
        update_editable_field={@props.update_editable_field}
        save_editable_field={@props.save_editable_field}
        tabIndex={@props.tabIndex}
        size={size}
        type={type}
        min_date={min_date}
        max_date={max_date}
        min_time={min_time}
        max_time={max_time}
        {...props}
      />
    )

  create_select_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    title = props.title or @props.column.title or column_key
    options = props.options or item.choices[column_key] or []

    column = props.column or @get_column()
    item.help ?= {}
    help = props.help or item.help[column_key] or column.help

    converter = @ZPUBLISHER_CONVERTER["select"]
    fieldname = name + converter

    selected = props.selected or @is_selected()
    disabled = props.disabled or @is_disabled()
    required = props.required or @is_required()
    size = props.size or @get_size()
    css_class = props.css_class or "form-control form-control-sm"
    if required then css_class += " required"

    return (
      <Select
        key={name + formatted_value}
        uid={uid}
        item={item}
        name={fieldname}
        defaultValue={value}
        column_key={column_key}
        title={title}
        help={help}
        disabled={disabled}
        selected={selected}
        required={required}
        options={options}
        className={css_class}
        update_editable_field={@props.update_editable_field}
        save_editable_field={@props.save_editable_field}
        tabIndex={@props.tabIndex}
        size={size}
        {...props}
      />
    )

  create_multichoice_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    title = props.title or @props.column.title or column_key
    options = props.options or item.choices[column_key] or []

    column = props.column or @get_column()
    item.help ?= {}
    help = props.help or item.help[column_key] or column.help

    converter = @ZPUBLISHER_CONVERTER["multichoice"]
    fieldname = name + converter

    selected = props.selected or @is_selected()
    disabled = props.disabled or @is_disabled()
    required = props.required or @is_required()
    size = props.size or @get_size()
    css_class = props.css_class or "form-control form-control-sm"
    if required then css_class += " required"

    return (
      <MultiChoice
        key={name + formatted_value}
        uid={uid}
        item={item}
        name={fieldname}
        defaultValue={value}
        column_key={column_key}
        title={title}
        help={help}
        disabled={disabled}
        selected={selected}
        required={required}
        options={options}
        className={css_class}
        update_editable_field={@props.update_editable_field}
        save_editable_field={@props.save_editable_field}
        tabIndex={@props.tabIndex}
        size={size}
        {...props}
      />
    )

  create_multiselect_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    title = props.title or @props.column.title or column_key
    options = item.choices[column_key] or []
    duplicates = @get_type() == "multiselect_duplicates"

    column = props.column or @get_column()
    item.help ?= {}
    help = props.help or item.help[column_key] or column.help

    converter = @ZPUBLISHER_CONVERTER["multiselect"]
    fieldname = name + converter

    selected = props.selected or @is_selected()
    disabled = props.disabled or @is_disabled()
    required = props.required or @is_required()
    size = props.size or @get_size()
    css_class = props.css_class or "form-control form-control-sm"
    if required then css_class += " required"

    return (
      <MultiSelect
        key={name + formatted_value}
        uid={uid}
        item={item}
        name={fieldname}
        defaultValue={value}
        value={value}
        column_key={column_key}
        title={title}
        help={help}
        disabled={disabled}
        selected={selected}
        required={required}
        options={options}
        duplicates={duplicates}
        className={css_class}
        update_editable_field={@props.update_editable_field}
        save_editable_field={@props.save_editable_field}
        tabIndex={@props.tabIndex}
        size={size}
        {...props}
      />
    )

  create_multivalue_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    title = props.title or @props.column.title or column_key

    column = props.column or @get_column()
    item.help ?= {}
    help = props.help or item.help[column_key] or column.help

    converter = @ZPUBLISHER_CONVERTER["multivalue"]
    fieldname = name + converter

    selected = props.selected or @is_selected()
    disabled = props.disabled or @is_disabled()
    required = props.required or @is_required()
    size = props.size or @get_size()
    css_class = props.css_class or "form-control form-control-sm"
    if required then css_class += " required"

    return (
      <MultiValue
        key={name + formatted_value}
        uid={uid}
        item={item}
        name={fieldname}
        defaultValue={value}
        value={value}
        column_key={column_key}
        title={title}
        help={help}
        disabled={disabled}
        selected={selected}
        required={required}
        className={css_class}
        update_editable_field={@props.update_editable_field}
        save_editable_field={@props.save_editable_field}
        tabIndex={@props.tabIndex}
        size={size}
        {...props}
      />
    )

  # Creates a tiered_multivalue field component
    create_tiered_multivalue_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()   # <- 这里拿到的是后台的字符串
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    title = props.title or @props.column.title or column_key

    column = props.column or @get_column()
    item.help ?= {}
    help = props.help or item.help[column_key] or column.help

    converter = @ZPUBLISHER_CONVERTER["tiered_multivalue"] or ":list"
    fieldname = name + converter

    rows = null
    labels = null
    if @is_interimfield()
      interim = item[column_key] or {}
      rows = interim.rows or null
      labels = interim.labels or null

    # 打印看看 TableCell 拿到的到底是什么
#    console.log "[TableCell/tiered] uid=", uid, "key=", column_key, "value=", value

    selected = props.selected or @is_selected()
    disabled = props.disabled or @is_disabled()
    required = props.required or @is_required()
    size = props.size or @get_size()

    <TieredMultiValue
      key={name + (formatted_value or "")}
      uid={uid}
      item={item}
      name={fieldname}
      value={value}
      defaultValue={value}
      column_key={column_key}
      title={title}
      help={help}
      disabled={disabled}
      selected={selected}
      required={required}
      className="form-control form-control-sm"
      update_editable_field={@props.update_editable_field}
      save_editable_field={@props.save_editable_field}
      tabIndex={@props.tabIndex}
      size={size}
      rows={rows}
      labels={labels}
      {...props}
    />

  create_posneg_with_note_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    title = props.title or @props.column.title or column_key

    column = props.column or @get_column()
    item.help ?= {}
    help = props.help or item.help[column_key] or column.help

    converter = @ZPUBLISHER_CONVERTER["posneg_with_note"] or ":records"
    fieldname = name + converter

    # ===== 把后端的 dict 转成数组 =====
    raw_opts = item.choices?[column_key] or []
    options = []
    if Array.isArray(raw_opts)
      options = raw_opts
    else
      # raw_opts 是 {A: "阳性", B: "阴性", ...}
      for own val, text of raw_opts
        options.push(
          ResultValue: val
          ResultText: text
          ResultDescription: text
        )

    selected = props.selected or @is_selected()
    disabled = props.disabled or @is_disabled()
    required = props.required or @is_required()
    size = props.size or @get_size()
    css_class = props.css_class or "form-control form-control-sm"
    if required then css_class += " required"

    return (
      <PosNegWithNote
        key={name + (formatted_value or "")}
        uid={uid}
        item={item}
        name={fieldname}
        value={value}
        column_key={column_key}
        title={title}
        help={help}
        disabled={disabled}
        selected={selected}
        required={required}
        className={css_class}
        update_editable_field={@props.update_editable_field}
        save_editable_field={@props.save_editable_field}
        tabIndex={@props.tabIndex}
        size={size}
        formId={@props.formId}
        options={options}
        {...props}
      />
    )

  create_file_field: (options={}) ->
    props = options.props or {}
    column_key   = props.column_key   or @get_column_key()
    item         = props.item         or @get_item()
    name         = props.name         or @get_name()
    uid          = props.uid          or @get_uid()
    title        = props.title        or @props.column.title or column_key

    filename     = ""
    download_url = ""
    if @is_interimfield()
      interim      = item[column_key] or {}
      filename     = interim.filename     or ""
      download_url = interim.download_url or ""

    disabled = props.disabled or @is_disabled()

    <FileField
      key={name + (filename or uid)}
      uid={uid}
      item={item}
      name={name}
      column_key={column_key}
      title={title}
      filename={filename}
      download_url={download_url}
      disabled={disabled}
      update_editable_field={@props.update_editable_field}
      save_editable_field={@props.save_editable_field}
      {...props}
    />

  create_tcr_selector_field: (options={}) ->
    props = options.props or {}
    column_key = props.column_key or @get_column_key()
    item       = props.item       or @get_item()
    name       = props.name       or @get_name()
    uid        = props.uid        or @get_uid()

    columns = []
    rows    = []
    if @is_interimfield()
      interim  = item[column_key] or {}
      columns  = interim.columns  or []
      rows     = interim.rows     or []

    disabled = props.disabled or @is_disabled()

    <TcrSelector
      key={name + uid}
      uid={uid}
      item={item}
      name={name}
      column_key={column_key}
      columns={columns}
      rows={rows}
      disabled={disabled}
      update_editable_field={@props.update_editable_field}
      save_editable_field={@props.save_editable_field}
      {...props}
    />

  create_tcr_preparation_field: (options={}) ->
    props = options.props or {}
    column_key = props.column_key or @get_column_key()
    item       = props.item       or @get_item()
    name       = props.name       or @get_name()
    uid        = props.uid        or @get_uid()

    columns = []
    rows    = []
    if @is_interimfield()
      interim  = item[column_key] or {}
      columns  = interim.columns  or []
      rows     = interim.rows     or []

    disabled = props.disabled or @is_disabled()

    <TcrPreparation
      key={name + uid}
      uid={uid}
      item={item}
      name={name}
      column_key={column_key}
      columns={columns}
      rows={rows}
      disabled={disabled}
      update_editable_field={@props.update_editable_field}
      save_editable_field={@props.save_editable_field}
      {...props}
    />

  create_tcr_scaffold_field: (options={}) ->
    props = options.props or {}
    column_key = props.column_key or @get_column_key()
    item       = props.item       or @get_item()
    name       = props.name       or @get_name()
    uid        = props.uid        or @get_uid()

    columns = []
    rows    = []
    if @is_interimfield()
      interim  = item[column_key] or {}
      columns  = interim.columns  or []
      rows     = interim.rows     or []

    disabled = props.disabled or @is_disabled()

    <TcrScaffold
      key={name + uid}
      uid={uid}
      item={item}
      name={name}
      column_key={column_key}
      columns={columns}
      rows={rows}
      disabled={disabled}
      update_editable_field={@props.update_editable_field}
      save_editable_field={@props.save_editable_field}
      {...props}
    />
  create_tcr_plasmid_field: (options={}) ->
    props = options.props or {}
    column_key = props.column_key or @get_column_key()
    item       = props.item       or @get_item()
    name       = props.name       or @get_name()
    uid        = props.uid        or @get_uid()

    columns = []
    rows    = []
    if @is_interimfield()
      interim  = item[column_key] or {}
      columns  = interim.columns  or []
      rows     = interim.rows     or []

    disabled = props.disabled or @is_disabled()

    <TcrPlasmid
      key={name + uid}
      uid={uid}
      item={item}
      name={name}
      column_key={column_key}
      columns={columns}
      rows={rows}
      disabled={disabled}
      update_editable_field={@props.update_editable_field}
      save_editable_field={@props.save_editable_field}
      {...props}
    />

  create_checkbox_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    title = props.title or @props.column.title or column_key

    column = props.column or @get_column()
    item.help ?= {}
    help = props.help or item.help[column_key] or column.help

    converter = @ZPUBLISHER_CONVERTER["boolean"]
    fieldname = name + converter

    selected = props.selected or @is_selected()
    disabled = props.disabled or @is_disabled()
    required = props.required or @is_required()
    size = props.size or @get_size()
    css_class = props.css_class or "checkbox"
    if required then css_class += " required"

    return (
      <Checkbox
        key={name + formatted_value}
        uid={uid}
        item={item}
        name={fieldname}
        value="on"
        column_key={column_key}
        title={title}
        help={help}
        defaultChecked={value}
        disabled={disabled}
        className={css_class}
        update_editable_field={@props.update_editable_field}
        save_editable_field={@props.save_editable_field}
        tabIndex={@props.tabIndex}
        size={size}
        {...props}
      />
    )

  create_tick_field: ({props}={}) ->
    props ?= {}
    column_key = props.column_key or @get_column_key()
    item = props.item or @get_item()
    name = props.name or @get_name()
    value = props.value or @get_value()
    formatted_value = props.formatted_value or @get_formatted_value()
    uid = props.uid or @get_uid()
    title = props.title or @props.column.title or column_key

    column = props.column or @get_column()
    item.help ?= {}
    help = props.help or item.help[column_key] or column.help

    converter = @ZPUBLISHER_CONVERTER["tick"]
    fieldname = name + converter

    selected = props.selected or @is_selected()
    disabled = props.disabled or @is_disabled()
    required = props.required or @is_required()
    size = props.size or @get_size()
    css_class = props.css_class or "checkbox tick-checkbox"  # 添加独立样式类
    if required then css_class += " required"

    return (
      <Tick
        key={name + formatted_value}
        uid={uid}
        item={item}
        name={fieldname}
        defaultChecked={value}
        column_key={column_key}
        disabled={disabled}
        className={css_class}
        update_editable_field={@props.update_editable_field}
        save_editable_field={@props.save_editable_field}
        {...props}
      />
    )

  render_content: ->
    column_key = @get_column_key()
    item = @get_item()
    unless item
      console.warn "Skipping empty folderitem for column '#{column_key}'"
      return null

    type = @get_type()
    field = []

    if type == "readonly"
      field = field.concat @create_readonly_field()
    else if type == "calculated"
      field = field.concat @create_calculated_field()
    else if type in ["select", "choices"]
      field = field.concat @create_select_field()
    else if type in ["multichoice"]
      field = field.concat @create_multichoice_field()
    else if type in ["multiselect", "multiselect_duplicates"]
      field = field.concat @create_multiselect_field()
    else if type in ["multivalue"]
      field = field.concat @create_multivalue_field()
    else if type == "boolean"
      field = field.concat @create_checkbox_field()
    else if type == "numeric"
      field = field.concat @create_numeric_field()
    else if type == "string"
      field = field.concat @create_string_field()
    else if type == "text"
      field = field.concat @create_text_field()
    else if type in ["date", "datetime"]
      field = field.concat @create_datetime_field()
    else if type == "fraction"
      field = field.concat @create_fraction_field()
    else if type in ["tiered_multivalue", "multivalue:tiered"]
      field = field.concat @create_tiered_multivalue_field()
    else if type == "file"
      field = field.concat @create_file_field()
    else if type == "tcr_selector"
      field = field.concat @create_tcr_selector_field()
    else if type == "tcr_preparation"
      field = field.concat @create_tcr_preparation_field()
    else if type == "tcr_scaffold"
      field = field.concat @create_tcr_scaffold_field()
    else if type == "tcr_plasmid"
      field = field.concat @create_tcr_plasmid_field()
    else if type == "posneg_with_note"
      field = field.concat @create_posneg_with_note_field()
    else if type == "tick"
      field = field.concat @create_tick_field()
    else
      field = field.concat @create_numeric_field()

    field

  render: ->
    <td className={@props.className}
        colSpan={@props.colspan}
        rowSpan={@props.rowspan}>
      <div className="form-group">
        {@render_before_content()}
        {@render_content()}
        {@render_after_content()}
      </div>
    </td>

export default TableCell
