import React from "react"
import Checkbox from "./Checkbox.coffee"
import TableCell from "./TableCell.coffee"
import TableTransposedCell from "./TableTransposedCell.coffee"

class TableCells extends React.Component

  constructor: (props) ->
    super(props)
    @on_remarks_expand_click = @on_remarks_expand_click.bind @

  on_remarks_expand_click: (event) ->
    event.preventDefault()
    el = event.currentTarget
    uid = el.getAttribute "uid"
    if @props.on_remarks_expand_click
      @props.on_remarks_expand_click uid

  get_column: (column_key) -> @props.columns[column_key]
  get_item: -> @props.item
  get_uid: -> @get_item().uid

  get_tab_index: (column_key, item) ->
    tabindex = item.tabindex or {column_key: "active"}
    tabindex = tabindex[column_key]
    if tabindex == "disabled" then -1 else 0

  get_colspan: (column_key, item) ->
    colspan = item.colspan or {}
    colspan[column_key]

  get_rowspan: (column_key, item) ->
    rowspan = item.rowspan or {}
    rowspan[column_key]

  skip_cell_rendering: (column_key) ->
    item = @get_item()
    skip = item.skip or []
    column_key in skip

  show_select: ->
    item = @get_item()
    if typeof item.show_select == "boolean"
      return item.show_select
    @props.show_select_column

  is_transposed: (column_key) ->
    column = @get_column column_key
    column.type == "transposed"

  get_transposed_items: () ->
    item = @get_item()
    transposed_keys = item.transposed_keys or []
    transposed_keys.map (key) -> item[key]

  has_transposed_items: () -> @get_transposed_items().length > 0
  is_transposed_item: () -> @get_item().hasOwnProperty "transposed_keys"

  is_loading: (uid) ->
    loading_uids = this.props.loading_uids or []
    loading_uids.indexOf(uid) > -1

  get_errors_for: (uid) ->
    errors = this.props.errors or {}
    errors[uid] or []

  create_multi_select_cell: (uids) ->
    uids ?= []
    return @create_placeholder_cell() unless uids.length > 0
    value = uids.join(",")
    item = @get_item()
    level = item.node_level or 0
    all_selected = uids.every (uid) => @props.selected_uids.includes(uid)
    (
      <td key={value} className="level-#{level}">
        <Checkbox
          value={value}
          tabIndex="-1"
          checked={all_selected}
          onChange={@props.on_multi_select_checkbox_checked}/>
      </td>
    )

  create_select_cell: () ->
    uid = @get_uid()
    return @create_placeholder_cell() unless uid
    checkbox_name = "#{@props.select_checkbox_name}:list"
    item = @get_item()
    remarks = @props.remarks
    level = item.node_level or 0
    loading = @is_loading(uid)
    errors = @get_errors_for(uid)
    (
      <td key={uid} className="level-#{level}">
        {!loading &&
          <Checkbox
            name={checkbox_name}
            value={uid}
            disabled={@props.disabled}
            checked={@props.selected}
            tabIndex="-1"
            onChange={@props.on_select_checkbox_checked}/>}
        {loading &&
          <span className="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>}
        {errors.length > 0 &&
          <span className="text-warning fas fa-exclamation-triangle"
                title={errors.join("\n")} />}
        {remarks &&
          <a uid={uid}
             href="#"
             className="remarks"
             onClick={@on_remarks_expand_click}>
            <span className="remarksicon fas fa-comment-alt"/>
          </a>}
      </td>
    )

  create_placeholder_cell: () -> <td className="placeholder"></td>

  create_regular_cell: (column_key, column_index) ->
    item = @get_item()
    column = @get_column column_key
    colspan = @get_colspan column_key, item
    rowspan = @get_rowspan column_key, item
    tabindex = @get_tab_index column_key, item
    css = "contentcell #{column_key}"
    <TableCell
      {...@props}
      key={column_index}
      item={item}
      column_key={column_key}
      column_index={column_index}
      column={column}
      colspan={colspan}
      rowspan={rowspan}
      className={css}
      tabIndex={tabindex}
    />

  create_transposed_cell: (column_key, column_index) ->
    item = @get_item()
    column = @get_column column_key
    colspan = @get_colspan column_key, item
    rowspan = @get_rowspan column_key, item
    tabindex = @get_tab_index column_key, item
    css = "contentcell #{column_key}"
    <TableTransposedCell
      {...@props}
      key={column_index}
      item={item}
      column_key={column_key}
      column_index={column_index}
      column={column}
      colspan={colspan}
      rowspan={rowspan}
      on_remarks_expand_click={@on_remarks_expand_click}
      className={css}
      tabIndex={tabindex}
    />

  create_dnd_cell: () ->
    item = @get_item()
    uid = @get_uid()
    level = item.node_level or 0
    (
      <td ref={@props.dragref} key="dnd" className="level-#{level} dnd">
        <i className="fas fa-sort"></i>
      </td>
    )

  build_cells: ->
    cells = []
    hidden = @props.hideColumns or []

    # insert select column
    if @show_select() and not @is_transposed_item()
      cells.push @create_select_cell()
    else if @show_select() and @is_transposed_item()
      items = @get_transposed_items()
      uids = items.map (item) -> item.uid
      cells.push @create_multi_select_cell uids

    # dnd
    if @props.allow_row_reorder
      cells.push @create_dnd_cell()

    # 业务列
    for column_key, column_index in @props.visible_columns
      column = @props.columns[column_key]

      # 跳过 rowspans
      if @skip_cell_rendering column_key
        continue

      if column? and (column.type is "multivalue:tiered" or column.type is "tiered_multivalue")
        continue
      if column? and column.type is "grouped_fields"
        continue
      if column_key in hidden
        continue

      if @is_transposed column_key
        cells.push @create_transposed_cell column_key, column_index
      else
        cells.push @create_regular_cell column_key, column_index

    return cells


  render: -> @build_cells()

export default TableCells
