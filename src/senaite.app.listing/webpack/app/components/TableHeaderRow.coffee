import React from "react"

import Checkbox from "./Checkbox.coffee"
import TableHeaderCell from "./TableHeaderCell.coffee"

class TableHeaderRow extends React.Component

  constructor: (props) ->
    super(props)
    @on_header_column_click = @on_header_column_click.bind @

  on_header_column_click: (event) ->
    el = event.currentTarget
    index = el.getAttribute "index"
    sort_order = el.getAttribute "sort_order"
    return unless index

    console.debug "HEADER CLICKED sort_on='#{index}' sort_order=#{sort_order}"

    if "active" in el.classList
      if sort_order == "ascending"
        sort_order = "descending"
      else
        sort_order = "ascending"

    @props.on_header_column_click index, sort_order

  is_required_column: (key) ->
    folderitems = @props.folderitems or []
    return no unless folderitems.length
    first_item = folderitems[0]
    required = first_item.required or []
    key in required

  is_sortable: (column, key) ->
    return no if column.sortable is no
    return yes if column.index
    return yes if key in @props.sortable_columns
    no

  build_cells: ->
    cells = []

    # 选择框
    if @props.show_select_column
      show_select_all_checkbox = @props.show_select_all_checkbox
      cells.push(
        <th className="select-column" key="select_all">
          {show_select_all_checkbox and
          <Checkbox
            name="select_all"
            value="all"
            checked={@props.all_items_selected}
            onChange={@props.on_select_checkbox_checked}/>}
        </th>
      )

    # DnD 列
    if @props.allow_row_reorder
      cells.push(
        <th className="dnd-column" key="dnd"></th>
      )

    # 真正的业务列
    for key in @props.visible_columns
      column = @props.columns[key]

      # 前端根据类型过滤表头
      if column? and (column.type is "multivalue:tiered" or column.type is "tiered_multivalue")
        continue

      # 分组字段：隐藏列头，在行里分组展示
      if column? and column.type is "grouped_fields"
        continue

      sortable = @is_sortable column, key
      index = column.index or key
      title = column.title
      alt = column.alt or title

      sort_on = @props.sort_on or "created"
      sort_order = @props.sort_order or "ascending"
      is_sort_column = index is sort_on
      required = @is_required_column key

      cls = [key]
      if sortable then cls.push "sortable"
      if is_sort_column and sortable
        cls.push "active #{sort_order}"
      if required then cls.push "required"
      cls = cls.join " "

      cells.push(
        <TableHeaderCell
          key={key}
          {...@props}
          title={title}
          alt={alt}
          index={index}
          sort_order={sort_order}
          className={cls}
          onClick={if sortable then @on_header_column_click else undefined}
        />
      )

    cells

  render: ->
    <tr onContextMenu={@props.on_context_menu}>
      {@build_cells()}
    </tr>

export default TableHeaderRow
