# Tick.coffee
import React from "react"

class Tick extends React.Component
  constructor: (props) ->
    super(props)
    @state = {checked: !!props.defaultChecked}
    @on_change = @on_change.bind @

  on_change: (event) ->
    checked = event.target.checked
    @setState {checked: checked}
    if @props.update_editable_field
      @props.update_editable_field @props.uid, @props.column_key, checked, @props.item
    if @props.save_editable_field
      @props.save_editable_field @props.uid, @props.column_key, checked, @props.item

  render: ->
    <input type="checkbox"
      name={@props.name}
      checked={@state.checked}
      onChange={@on_change}
      disabled={@props.disabled}
      className={@props.className} />

export default Tick