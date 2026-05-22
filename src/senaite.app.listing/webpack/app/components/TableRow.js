import React from "react"
import {useCallback, useRef, memo} from "react"
import TableCells from "./TableCells.coffee"
import TieredMultiValue from "./TieredMultiValue.coffee"
import {ItemTypes} from "./Constants"
import {useDrag} from "react-dnd"
import {useDrop} from "react-dnd"
import TcrSelector from "./TcrSelector.coffee"
import TcrPreparation from "./TcrPreparation.coffee";
import TcrScaffold from "./TcrScaffold.coffee";
import TcrPlasmid from "./TcrPlasmid.coffee";


const TableRow = memo(function TableRow({...props}) {
    const dragRef = useRef(null)
    const dropRef = useRef(null)

    const moveRow = useCallback(
        (from_index, to_index) => {
            if (props.move_row) {
                props.move_row(from_index, to_index)
            }
        },
        [props]
    )

    const onRowOrderChange = useCallback(
        () => {
            if (props.on_row_order_change) {
                props.on_row_order_change()
            }
        },
        [props]
    )

    const [{}, drop] = useDrop({
        accept: ItemTypes.ROW,
        hover(item, monitor) {
            if (!dragRef.current) return
            const dragIndex = item.index
            const hoverIndex = props.row_index
            if (dragIndex === hoverIndex) return
            moveRow(dragIndex, hoverIndex)
            item.index = hoverIndex
        }
    })

    const [{isDragging}, drag, preview] = useDrag({
        type: ItemTypes.ROW,
        item: () => ({
            uid: props.uid,
            category: props.category,
            index: props.row_index,
        }),
        canDrag: () => props.allow_row_reorder,
        collect: (monitor) => ({
            isDragging: !!monitor.isDragging()
        }),
        end: () => {
            onRowOrderChange()
        }
    })

    preview(drop(dropRef))
    drag(dragRef)

    let css_class = props.className
    if (isDragging) {
        css_class += " dragging"
    }

    function on_context_menu(e) {
        if (props.on_row_context_menu) {
            props.on_row_context_menu(e, props.item)
        }
    }

    // ======================================================
    // 识别这一行里哪些列是真正的 tiered_multivalue
    // 只在「当前行有 tiered 字段」的情况下才返回 key
    // ======================================================
    const columns = props.columns || {}
    const item = props.item || {}
    const colKeys = Object.keys(columns)

    const tieredKeys = colKeys.filter((k) => {
        const colDef = columns[k]
        if (!colDef) return false

        const colIsTiered =
            colDef.type === "multivalue:tiered" ||
            colDef.type === "tiered_multivalue"

        if (!colIsTiered) {
            return false
        }
        const field = item[k]

        // qPCR 那一行这里通常是字符串 / 简单值，
        // 病理那一行才是 {value, rows, labels, result_type: "tiered_multivalue", ...}
        if (!field || typeof field !== "object") {
            return false
        }

        const rt = field.result_type || field.type
        const fieldIsTiered =
            rt === "tiered_multivalue" ||
            rt === "multivalue:tiered"

        return fieldIsTiered
    })

    const tcrKeys = colKeys.filter((k) => {
        const colDef = columns[k]
        if (!colDef) return false
        if (colDef.type !== "tcr_selector") return false
        const field = item[k]
        if (!field || typeof field !== "object") return false
        const rt = field.result_type || field.type
        return rt === "tcr_selector"
    })

    const preparationKeys = colKeys.filter((k) => {
        const colDef = columns[k]
        if (!colDef) return false
        if (colDef.type !== "tcr_preparation") return false
        const field = item[k]
        if (!field || typeof field !== "object") return false
        const rt = field.result_type || field.type
        return rt === "tcr_preparation"
    })

    const scaffoldKeys = colKeys.filter((k) => {
        const colDef = columns[k]
        if (!colDef) return false
        if (colDef.type !== "tcr_scaffold") return false
        const field = item[k]
        if (!field || typeof field !== "object") return false
        const rt = field.result_type || field.type
        return rt === "tcr_scaffold"
    })

    const plasmidKeys = colKeys.filter((k) => {
        const colDef = columns[k]
        if (!colDef) return false
        if (colDef.type !== "tcr_plasmid") return false
        const field = item[k]
        if (!field || typeof field !== "object") return false
        const rt = field.result_type || field.type
        return rt === "tcr_plasmid"
    })

    let fieldGroups = null
    if (item && item._lp_field_groups && Array.isArray(item._lp_field_groups)) {
        fieldGroups = item._lp_field_groups
    } else if (item && item._lp_field_groups_json) {
        try {
            fieldGroups = JSON.parse(item._lp_field_groups_json)
        } catch (e) {
        }
    }

    // 主行：把要隐藏的列（真正 tiered 的那几个）传给 TableCells
    const mainRow = (
        <tr
            className={css_class}
            ref={dropRef}
            onClick={props.onClick}
            onContextMenu={on_context_menu}
            category={props.category}
            uid={props.uid}
        >
            <TableCells
                dragref={dragRef}
                {...props}
                // hideColumns={tieredKeys}
                hideColumns={tieredKeys.concat(tcrKeys).concat(preparationKeys).concat(scaffoldKeys).concat(plasmidKeys)}
            />
        </tr>
    )


    // 有分组字段：在主行下方渲染分组表单 <tr>
    if (fieldGroups && fieldGroups.length > 0) {
        const colSpanAll = props.columns_count || colKeys.length

        const groupRow = (
            <tr
                key={props.uid + "-grouped-fields"}
                className={(props.className || "") + " lp-grouped-fields-row"}
            >
                <td colSpan={colSpanAll} style={{padding: "0"}}>
                    <div className="lp-grouped-fields-wrap">
                        {fieldGroups.map((group, gi) => (
                            <div key={gi} className="lp-grouped-fields-group">
                                <div className="lp-grouped-fields-title">{group.title}</div>
                                <div className="lp-grouped-fields-cells">
                                    {group.fields.map((f, fi) => {
                                        const fieldData = item[f.keyword] || {}
                                        const val = fieldData.value !== undefined ? fieldData.value : (fieldData.formatted_value || "")
                                        const canEdit = item && Array.isArray(item.allow_edit) && item.allow_edit.indexOf(f.keyword) !== -1
                                        return (
                                            <div key={fi} className="lp-grouped-field">
                                                <span className="lp-grouped-field-label"
                                                      title={f.title}>{f.title}</span>
                                                {canEdit ? (
                                                    <input
                                                        type="text"
                                                        className="lp-grouped-field-input"
                                                        defaultValue={val}
                                                        onBlur={(e) => {
                                                            if (props.update_editable_field) {
                                                                props.update_editable_field(props.uid, f.keyword, e.target.value, item)
                                                            }
                                                        }}
                                                    />
                                                ) : (
                                                    <span className="lp-grouped-field-val">{val || " "}</span>
                                                )}
                                            </div>
                                        )
                                    })}
                                </div>
                            </div>
                        ))}
                    </div>
                </td>
            </tr>
        )

        return [mainRow, groupRow]
    }

    if (tieredKeys.length === 0 && tcrKeys.length === 0 && preparationKeys.length === 0 && scaffoldKeys.length === 0 && plasmidKeys.length === 0 ) {
        return mainRow
    }

    const colSpan = props.columns_count || colKeys.length
    const tcrColSpan = (props.columns_count || colKeys.length) * 2;

    const extraRows = tieredKeys.map((key) => {
        const colDef = columns[key] || {}
        const field = item ? item[key] : null

        const rawValue = field
            ? (field.value || field.formatted_value || "")
            : ""

        const rows = (field && field.rows) || colDef.rows || 6
        const labels = (field && field.labels) || colDef.labels || []
        const title = (field && field.title) || colDef.title || key

        const canEdit =
            item &&
            Array.isArray(item.allow_edit) &&
            item.allow_edit.indexOf(key) !== -1

        const readonly = !canEdit
        const disabled = !canEdit || item.disabled === true

        return (
            <tr
                key={props.uid + "-" + key + "-tiered"}
                className={(props.className || "") + " tiered-multivalue-row"}
            >
                <td colSpan={colSpan}>
                    <div style={{fontWeight: "bold", marginBottom: "4px"}}>
                        {title}
                    </div>
                    <TieredMultiValue
                        uid={props.uid}
                        item={item}
                        name={key}
                        column_key={key}
                        defaultValue={rawValue}
                        labels={labels}
                        rows={rows}
                        readonly={readonly}
                        disabled={disabled}
                        update_editable_field={props.update_editable_field}
                    />
                </td>
            </tr>
        )
    })

    const tcrRows = tcrKeys.map((key) => {
        const colDef = columns[key] || {}
        const field = item ? item[key] : null
        const title = (field && field.title) || colDef.title || key
        const cols = (field && field.columns) || []
        const rows = (field && field.rows) || []

        const canEdit =
            item &&
            Array.isArray(item.allow_edit) &&
            item.allow_edit.indexOf(key) !== -1

        const disabled = !canEdit || item.disabled === true

        return (
            <tr
                key={props.uid + "-" + key + "-tcr"}
                className={(props.className || "") + " tcr-selector-row"}
            >
                <td colSpan={999} style={{padding: "0", borderTop: "1px solid #dde3ea", maxWidth: "0", width: "100%"}}>
                    <TcrSelector
                        uid={props.uid}
                        item={item}
                        name={key}
                        column_key={key}
                        columns={cols}
                        rows={rows}
                        disabled={disabled}
                        update_editable_field={props.update_editable_field}
                        save_editable_field={props.save_editable_field}
                    />
                </td>
            </tr>
        )
    })

    const preparationRows = preparationKeys.map((key) => {
        const colDef = columns[key] || {}
        const field = item ? item[key] : null
        const cols = (field && field.columns) || []
        const rows = (field && field.rows) || []

        const canEdit =
            item &&
            Array.isArray(item.allow_edit) &&
            item.allow_edit.indexOf(key) !== -1

        const disabled = !canEdit || item.disabled === true

        return (
            <tr
                key={props.uid + "-" + key + "-preparation"}
                className={(props.className || "") + " tcr-preparation-row"}
            >
                <td colSpan={999} style={{padding: "0", borderTop: "1px solid #dde3ea"}}>
                    <TcrPreparation
                        uid={props.uid}
                        item={item}
                        name={key}
                        column_key={key}
                        columns={cols}
                        rows={rows}
                        disabled={disabled}
                        update_editable_field={props.update_editable_field}
                        save_editable_field={props.save_editable_field}
                    />
                </td>
            </tr>
        )
    })

    const scaffoldRows = scaffoldKeys.map((key) => {
        const colDef = columns[key] || {}
        const field = item ? item[key] : null
        const cols = (field && field.columns) || []
        const rows = (field && field.rows) || []

        const canEdit =
            item &&
            Array.isArray(item.allow_edit) &&
            item.allow_edit.indexOf(key) !== -1

        const disabled = !canEdit || item.disabled === true

        return (
            <tr
                key={props.uid + "-" + key + "-scaffold"}
                className={(props.className || "") + " tcr-scaffold-row"}
            >
                <td colSpan={999} style={{padding: "0", borderTop: "1px solid #dde3ea"}}>
                    <TcrScaffold
                        uid={props.uid}
                        item={item}
                        name={key}
                        column_key={key}
                        columns={cols}
                        rows={rows}
                        disabled={disabled}
                        update_editable_field={props.update_editable_field}
                        save_editable_field={props.save_editable_field}
                    />
                </td>
            </tr>
        )
    })

    const plasmidRows = plasmidKeys.map((key) => {
        const field = item ? item[key] : null
        const cols = (field && field.columns) || []
        const rows = (field && field.rows) || []
        const canEdit = item && Array.isArray(item.allow_edit) && item.allow_edit.indexOf(key) !== -1
        const disabled = !canEdit || item.disabled === true
        return (
            <tr key={props.uid + "-" + key + "-plasmid"}
                className={(props.className || "") + " tcr-plasmid-row"}>
                <td colSpan={999} style={{padding: "0", borderTop: "1px solid #dde3ea"}}>
                    <TcrPlasmid
                        uid={props.uid}
                        item={item}
                        name={key}
                        column_key={key}
                        columns={cols}
                        rows={rows}
                        disabled={disabled}
                        update_editable_field={props.update_editable_field}
                        save_editable_field={props.save_editable_field}
                    />
                </td>
            </tr>
        )
    })

return [mainRow].concat(extraRows).concat(tcrRows).concat(preparationRows).concat(scaffoldRows).concat(plasmidRows)

})

export default TableRow