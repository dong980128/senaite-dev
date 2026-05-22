import React from "react"

window.TieredMultiValue ?= {}

class window.TieredMultiValue.Editor
  constructor: (@td, opts = {}) ->
    @opts = opts
    @uid = opts.uid or ""
    @name = opts.name or ""
    @field_id = opts.field_id or @name
    @rows = parseInt(opts.rows or 6, 10)
    @labels = @normalizeLabels()
    @readonly = !!(opts.readonly or opts.disabled)
    @placeholder = (opts.placeholder ? "—")
    @values = @normalizeValues(opts.value)
    @base_url = (opts.base_url or window.location.pathname).replace(/\/+$/, '')
    @images = @normalizeImages(opts.images)

    @previewHost = opts.preview_host or null
    @previewGrid = null

    @previewMask = null
    @previewImg = null

    @inputs = []
    @render()

  normalizeLabels: ->
    defaultLabels = ['%1+', '%2+', '%3+', '%0', 'H-score', '总和']
    defaultLabels.slice(0, @rows)

  normalizeValues: (v) ->
    if not v?
      return [0, 0, 0, 100, 0, 100]
    try
      if typeof v is 'string' and v.trim().length
        arr = JSON.parse(v)
        if Array.isArray(arr) then v = arr
    catch e then null
    if Array.isArray(v)
      xs = v.slice(0, 6)
      while xs.length < 6 then xs.push(0)
      return xs
    [0, 0, 0, 100, 0, 100]

  normalizeImages: (imgs) ->
    out = []
    if Array.isArray(imgs)
      for x in imgs when x? and String(x).trim().length > 0
        out.push(x)
      return out
    if imgs? and typeof imgs is 'object'
      for k, v of imgs when v? and String(v).trim().length > 0
        out.push(v)
    out

  computeHScore: (xs) ->
    x1 = parseInt(xs[0] or 0, 10) or 0
    x2 = parseInt(xs[1] or 0, 10) or 0
    x3 = parseInt(xs[2] or 0, 10) or 0
    1 * x1 + 2 * x2 + 3 * x3

  computeTotal: (xs) ->
    (parseInt(xs[i] or 0, 10) or 0 for i in [0..2]).reduce(((a, b)->a + b), 0)

  updateDerived: ->
    @values[4] = @computeHScore(@values)
    @values[5] = @computeTotal(@values)

  _toRelativeUrl: (url) ->
    return null unless url?
    try url.replace(/^https?:\/\/[^/]+/, '')
    catch e then url

  _resolveImageUrl: (u) ->
    return null unless u?
    s = String(u)
    if /^https?:\/\//.test(s)
      return @_toRelativeUrl(s)
    if s.indexOf('/') is 0
      return s

    if s.indexOf('@@tmv-image') is 0
      base = (@base_url or '').replace(/\/+$/, '')
      return "#{base}/#{s}"
    s


  _ensureLightbox: ->
    return if @previewMask?

    mask = document.createElement('div')
    mask.className = 'tmv-preview-mask'
    mask.style.display = 'none'

    img = document.createElement('img')
    img.className = 'tmv-preview-zoom'
    mask.appendChild(img)

    @previewMask = mask
    @previewImg = img
    @zoomScale = 1
    @zoomTx = 0
    @zoomTy = 0

    close = (ev) =>
      ev?.preventDefault?()
      mask.style.display = 'none'
      @zoomScale = 1
      @zoomTx = 0
      @zoomTy = 0
      @_applyZoomTransform()
    mask.addEventListener 'contextmenu', (ev) -> ev.preventDefault()
    img.addEventListener 'contextmenu', (ev) -> ev.preventDefault()
    wheelHandler = (ev) =>
      ev.preventDefault?()
      dy = ev.deltaY ? 0
      factor = if dy > 0 then 0.9 else 1.1
      @zoomScale = (@zoomScale ? 1) * factor
      @zoomScale = Math.max(0.2, Math.min(6, @zoomScale))
      @_applyZoomTransform()

    try
      mask.addEventListener 'wheel', wheelHandler, {passive:false}
    catch err
      mask.addEventListener 'wheel', wheelHandler
    img.setAttribute('draggable', 'false')
    img.addEventListener 'dragstart', (ev) -> ev.preventDefault()

    dragging = false
    sx = 0; sy = 0
    stx = 0; sty = 0

    move = (ev) =>
      return unless dragging
      @zoomTx = stx + (ev.clientX - sx)
      @zoomTy = sty + (ev.clientY - sy)
      @_applyZoomTransform()

    up = (ev) =>
      return unless dragging
      dragging = false
      img.style.cursor = 'grab'
      document.removeEventListener 'mousemove', move
      document.removeEventListener 'mouseup', up

    img.addEventListener 'mousedown', (ev) =>
      return unless ev.button is 0
      ev.preventDefault()
      dragging = true
      sx = ev.clientX
      sy = ev.clientY
      stx = @zoomTx ? 0
      sty = @zoomTy ? 0
      img.style.cursor = 'grabbing'
      document.addEventListener 'mousemove', move
      document.addEventListener 'mouseup', up
    img.addEventListener 'dblclick', (ev) =>
      ev.preventDefault()
      @zoomScale = 1
      @zoomTx = 0
      @zoomTy = 0
      @_applyZoomTransform()
    img.addEventListener 'click', (ev) ->
      ev.stopPropagation()
    mask.addEventListener 'click', (ev) =>
      return unless ev.target is mask
      close(ev)

    document.addEventListener 'keyup', (ev) ->
      if ev.key is 'Escape' then close(ev)

    document.body.appendChild(mask)


  _showZoom: (url) ->
    return unless url?
    @_ensureLightbox()
    @zoomScale = 1
    @zoomTx = 0
    @zoomTy = 0
    @_applyZoomTransform()

    @previewImg.removeAttribute('src')
    @previewImg.onerror = => @previewMask.style.display = 'none'
    @previewImg.src = url
    @previewMask.style.display = 'flex'


  _applyZoomTransform: ->
    return unless @previewImg?
    s = @zoomScale ? 1
    tx = @zoomTx ? 0
    ty = @zoomTy ? 0
    @previewImg.style.transform = "translate(#{tx}px, #{ty}px) scale(#{s})"

  _refreshPreviewAll: ->
    return unless @previewGrid?
    @previewGrid.innerHTML = ''

    if not @images? or @images.length is 0
      empty = document.createElement('div')
      empty.className = 'tmv-preview-empty'
      empty.textContent = @placeholder
      @previewGrid.appendChild(empty)
      return

    for url in @images when url? and String(url).trim().length > 0
      src = @_resolveImageUrl(url)
      continue unless src? and String(src).trim().length > 0
      wrap = document.createElement('div')
      wrap.className = 'tmv-preview-item'

      img = document.createElement('img')
      img.className = 'tmv-preview-image'
      img.src = src
      img.style.cursor = 'zoom-in'
      do (zoomSrc = src) =>
        img.addEventListener 'click', (ev) =>
          ev.preventDefault()
          @_showZoom(zoomSrc)

      wrap.appendChild(img)
      @previewGrid.appendChild(wrap)

  uploadImages: (files, mode = 'replace', allowEmpty = false) ->
    xs = []
    if files?
      for f in files when f? then xs.push(f)

    return unless allowEmpty or xs.length > 0

    fd = new FormData()
    fd.append 'field', @field_id
    fd.append 'mode', mode

    for f in xs
      fd.append 'files', f

    xhr = new XMLHttpRequest()
    xhr.open 'POST', "#{@base_url}/@@tmv-upload", true
    xhr.onload = =>
      return unless xhr.status is 200
      try
        resp = JSON.parse(xhr.responseText)
        if resp?.ok and resp?.images?
          # 延迟 500ms 再加载图片，等待 ZODB 各 worker 连接同步到最新数据
          setTimeout(=>
            @images = []
            for it in resp.images when it?
              u = it.rel or it.url
              src = @_resolveImageUrl(u)
              if src? and String(src).trim().length > 0
                @images.push(src)

            @_refreshPreviewAll()
            if @opts?.onChange? then @opts.onChange(@_encodeForSubmit(), null)
          , 500)
      catch e then null
    xhr.send fd

  _encodeForSubmit: ->
    arr = @values.slice(0, 6)
    arr[4] = parseInt(arr[4] or 0, 10) or 0
    arr[5] = parseInt(arr[5] or 0, 10) or 0
    {values: arr, images: @images}

  serialize: -> JSON.stringify(@_encodeForSubmit())

  _on_cell_input: (idx, e) =>
    return if @readonly
    raw = e?.target?.value
    if raw is "" then v = "" else v = parseInt(raw, 10) or 0
    @values[idx] = v

    if idx in [0, 1, 2]
      v1 = parseInt(@values[0] or 0, 10) or 0
      v2 = parseInt(@values[1] or 0, 10) or 0
      v3 = parseInt(@values[2] or 0, 10) or 0
      rest = 100 - (v1 + v2 + v3)
      if rest < 0 then rest = 0
      @values[3] = rest
      if @inputs?[3]?
        @inputs[3].value = String(rest)

    @updateDerived()
    @refresh()

    if @opts?.onChange? then @opts.onChange(@_encodeForSubmit(), e)

  render: ->
    @td.innerHTML = ''
    if @previewHost?
      @previewHost.innerHTML = ''
    wrapper = document.createElement('div')
    wrapper.className = 'tiered-multivalue tmv-ihc'

    leftBox = document.createElement('div')
    leftBox.className = 'tmv-left'

    @hidden = document.createElement('input')
    @hidden.type = 'hidden'
    @hidden.name = @name
    @hidden.value = @serialize()
    leftBox.appendChild(@hidden)
    if not @readonly
      uploadBar = document.createElement('div')
      uploadBar.className = 'tmv-upload-bar'

      btn = document.createElement('button')
      btn.type = 'button'
      btn.className = 'tmv-upload-btn'
      btn.textContent = '上传图片'
      uploadBar.appendChild(btn)

      clearBtn = document.createElement('button')
      clearBtn.type = 'button'
      clearBtn.className = 'tmv-upload-clear'
      clearBtn.textContent = '清空'
      uploadBar.appendChild(clearBtn)

      multiInput = document.createElement('input')
      multiInput.type = 'file'
      multiInput.accept = 'image/*'
      multiInput.multiple = true
      multiInput.className = 'tmv-upload-input'
      multiInput.style.display = 'none'
      uploadBar.appendChild(multiInput)

      btn.addEventListener 'click', (ev) ->
        ev.preventDefault()
        multiInput.click()

      clearBtn.addEventListener 'click', (ev) =>
        ev.preventDefault()
        @uploadImages([], 'replace', true)

      multiInput.addEventListener 'change', (ev) =>
          fs = ev?.target?.files
          if not fs or fs.length is 0
              return
          @uploadImages(fs, 'replace', false)
          try multiInput.value = ""
          catch e then null

      leftBox.appendChild(uploadBar)

    table = document.createElement('table')
    table.className = 'tmv-table'
    tbody = document.createElement('tbody')
    table.appendChild(tbody)

    @updateDerived()

    for i in [0...@rows]
      tr = document.createElement('tr')

      th = document.createElement('th')
      th.className = 'tmv-label'
      th.textContent = @labels[i] ? "Row #{i + 1}"
      tr.appendChild(th)

      td = document.createElement('td')
      td.className = 'tmv-cell'

      if i < 4 and not @readonly
        input = document.createElement('input')
        input.type = 'number'
        input.className = 'tmv-input'
        input.value = if @values[i] is "" then "" else (@values[i] or 0)
        if i is 3
          input.readOnly = true

        @inputs[i] = input

        inputWrap = document.createElement('span')
        inputWrap.className = 'tmv-input-wrap'
        inputWrap.appendChild(input)
        td.appendChild(inputWrap)

        do (ii = i, inp = input) =>
          handler = (e) => @_on_cell_input(ii, e)
          inp.addEventListener 'change', handler
          inp.addEventListener 'keyup', handler
          if ii in [0, 1, 2]
            inp.addEventListener 'focus', (ev) ->
              val = ev.target.value
              if val is "0" then ev.target.value = ""

      else
        span = document.createElement('span')
        span.className = 'tmv-ro'
        span.dataset.idx = String(i)
        span.textContent = if @values[i]? then @values[i] else @placeholder
        td.appendChild(span)

      tr.appendChild(td)
      tbody.appendChild(tr)

    leftBox.appendChild(table)
    wrapper.appendChild(leftBox)
    if @previewHost?
      panel = document.createElement('div')
      panel.className = 'tmv-preview-panel'

      grid = document.createElement('div')
      grid.className = 'tmv-preview-grid'
      panel.appendChild(grid)

      @previewHost.appendChild(panel)
      @previewGrid = grid

      @_refreshPreviewAll()

    @td.appendChild(wrapper)

  refresh: ->
    ro5 = @td.querySelector('.tmv-ro[data-idx="4"]')
    ro6 = @td.querySelector('.tmv-ro[data-idx="5"]')
    if ro5? then ro5.textContent = @values[4]
    if ro6? then ro6.textContent = @values[5]
    @hidden.value = @serialize()


class TieredMultiValue extends React.Component
  constructor: (props) ->
    super(props)

    parsedStatus = "empty"
    parsedValues = [0, 0, 0, 100, 0, 100]
    parsedImages = []

    raw = props.value or props.defaultValue
    if typeof raw is 'string' and raw.trim().length
      try
        obj = JSON.parse(raw)
        if obj?.status then parsedStatus = obj.status
        if Array.isArray(obj?.values) then parsedValues = obj.values
        if obj?.images? then parsedImages = obj.images
      catch e then null
    if parsedImages? and typeof parsedImages is 'object' and not Array.isArray(parsedImages)
      tmp = []
      for k, v of parsedImages when v? and String(v).trim().length > 0
        tmp.push(v)
      parsedImages = tmp

    @state =
      filter: parsedStatus      # "detected" | "undetected" | "empty"
      values: parsedValues
      images: parsedImages

    @on_change = @on_change.bind(@)
    @on_filter_change = @on_filter_change.bind(@)

  _pushToBuffer: ->
    uid = @props.uid
    name = @props.column_key or @props.name
    vals = @state.values or [0, 0, 0, 100, 0, 100]
    imgs = @state.images or []

    window.__tmv_buffer ?= {}
    window.__tmv_buffer[uid] ?= {}

    payload =
      status: @state.filter or "empty"
      values: vals
      images: imgs

    window.__tmv_buffer[uid][name] = payload

    if @props.update_editable_field?
      @props.update_editable_field uid, name, payload, @props.item

  _ensureDefaultsForDetected: ->
    vals = @state.values or []
    isEmpty =
      not vals? or vals.length is 0 or vals.every (v) -> v is "" or v is null or v is undefined

    if isEmpty
      defaults = [0, 0, 0, 100, 0, 100]
      @setState {values: defaults}, =>
        @_pushToBuffer()
    else
      @_pushToBuffer()

  _applyUndetected: ->
    @setState {images: []}, =>
      @_pushToBuffer()

  on_change: (encoded, domEvent) ->
    return if @props.disabled or @props.readonly
    return unless @state.filter is "detected"

    vals = null
    imgs = null

    if Array.isArray(encoded)
      vals = encoded
      imgs = @state.images or []
    else
      vals = encoded?.values or [0, 0, 0, 100, 0, 100]
      imgs = encoded?.images or []
      if imgs? and typeof imgs is 'object' and not Array.isArray(imgs)
        tmp = []
        for k, v of imgs when v? and String(v).trim().length > 0
          tmp.push(v)
        imgs = tmp

    @setState {values: vals, images: imgs}, =>
      @_pushToBuffer()

  on_filter_change: (ev) ->
    val = ev.target.value or "empty"
    if @props.disabled or @props.readonly
      @setState {filter: val}
      return

    if val is "detected"
      @setState {filter: "detected"}, =>
        @_ensureDefaultsForDetected()
        @_create_editor()

    else if val is "undetected"
      @setState {filter: "undetected"}, =>
        @_applyUndetected()
        if @body? then @body.innerHTML = ""
        @editor = null

    else
      @setState {filter: "empty"}, =>
        if window? and @props.uid and (@props.column_key or @props.name)
          uid = @props.uid
          name = @props.column_key or @props.name
          if window.__tmv_buffer?[uid]?
            delete window.__tmv_buffer[uid][name]
        if @body? then @body.innerHTML = ""
        @editor = null

  _create_editor: ->
    return unless @body?

    viewOnly = !!(@props.readonly or @props.disabled or @props.editable is false)

    opts =
      uid: @props.uid
      name: @props.name
      field_id: @props.column_key or @props.name
      base_url: @props.item?.url or window.location.pathname
      rows: parseInt(@props.rows or @props.size or 6, 10)
      labels: @props.labels
      value: @state.values
      images: @state.images
      disabled: viewOnly
      readonly: viewOnly
      onChange: @on_change
      placeholder: @props.placeholder or "—"
      preview_host: @previewHost

    @body.innerHTML = ""
    @editor = new window.TieredMultiValue.Editor(@body, opts)

    if @host?
      sel = @host.querySelector(".tmv-filter select")
      isView = viewOnly or (sel? and sel.disabled is true)
      if isView then @host.classList.add("tmv-ihc--view") else @host.classList.remove("tmv-ihc--view")

  componentDidMount: ->
    if @state.filter is "detected"
      @_create_editor()

  componentDidUpdate: (prevProps, prevState) ->
    if prevState?.filter isnt @state.filter
      if @state.filter is "detected"
        @_create_editor()
      else
        if @body? then @body.innerHTML = ""
        @editor = null
      return

    if @host?
      sel = @host.querySelector(".tmv-filter select")
      viewOnly = !!(@props.readonly or @props.disabled or @props.editable is false)
      isView = viewOnly or (sel? and sel.disabled is true)
      if isView then @host.classList.add("tmv-ihc--view") else @host.classList.remove("tmv-ihc--view")

  render: ->
    cls = "tmv-root"
    if @props.disabled or @props.readonly or @props.editable is false
      cls += " tmv-root--view"

    <div className={cls} ref={(el) => @host = el}>
      <div className="tmv-filter">
        <div className="tmv-filter-row">
          {
            if @props.disabled or @props.readonly or @props.editable is false
              <select value={@state.filter} onChange={@on_filter_change} disabled={true}>
                <option value="empty">—</option>
                <option value="detected">检测</option>
                <option value="undetected">未检测</option>
              </select>
            else
              <select value={@state.filter} onChange={@on_filter_change}>
                <option value="empty">—</option>
                <option value="detected">检测</option>
                <option value="undetected">未检测</option>
              </select>
          }
        </div>

        {
          if @state.filter is "detected"
            <div className="tmv-body" ref={(el) => @body = el}/>
          else
            null
        }
      </div>

      {
        if @state.filter is "detected"
          <div className="tmv-preview-host" ref={(el) => @previewHost = el}/>
        else
          null
      }
    </div>

export default TieredMultiValue
