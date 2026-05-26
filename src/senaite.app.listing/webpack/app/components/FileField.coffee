import React from "react"

class FileField extends React.Component

  constructor: (props) ->
    super(props)

    @state =
      filename: props.filename or ""
      download_url: props.download_url or ""
      error: ""
      show_remote_picker: false
      subdirs: []
      selected_subdir: ""
      remote_loading: false
      fetching: false
      batch_count: 0
      batch_files: []

    @get_ar_url             = @get_ar_url.bind @
    @on_remote_picker_open  = @on_remote_picker_open.bind @
    @on_remote_picker_close = @on_remote_picker_close.bind @
    @on_subdir_select       = @on_subdir_select.bind @

  componentDidMount: ->
    # 若已有 filename（props 传入说明之前上传过），尝试从后端加载全部附件列表
    return unless @props.filename
    uid    = @props.uid
    ar_url = @get_ar_url()
    fetch ar_url + "/@@list-analysis-attachments?analysis_uid=" + uid,
      method: "GET"
      credentials: "include"
    .then (r) -> r.json()
    .then (resp) =>
      if resp.success and resp.attachments and resp.attachments.length > 1
        @setState
          batch_files: resp.attachments
          batch_count: resp.attachments.length
    .catch (e) =>
      console.warn "[FileField] load attachments failed", e

  componentDidUpdate: (prevProps) ->
    prev_filename = prevProps.filename or ""
    curr_filename = @props.filename or ""
    return if prev_filename is curr_filename
    @setState
      filename: curr_filename
      download_url: @props.download_url or ""
      batch_files: []
      batch_count: 0

  get_ar_url: ->
    url = window.location.href.split("?")[0].split("#")[0]
    url = url.replace(/\/@@[^\/]*$/, "")
    url

  get_authenticator: ->
    el = document.querySelector("input[name='_authenticator']")
    if el then el.value else ""

  on_remote_picker_open: ->
    uid    = @props.uid
    ar_url = @get_ar_url()
    @setState
      show_remote_picker: true
      remote_loading: true
      subdirs: []
      selected_subdir: ""
      error: ""

    fetch ar_url + "/@@list-remote-files?analysis_uid=" + uid,
      method: "GET"
      credentials: "include"
    .then (r) -> r.json()
    .then (resp) =>
      if resp.success
        @setState
          remote_loading: false
          subdirs: resp.subdirs or []
      else
        @setState
          remote_loading: false
          show_remote_picker: false
          error: resp.error or "获取目录列表失败"
    .catch (e) =>
      console.error "[FileField] list remote dirs error", e
      @setState
        remote_loading: false
        show_remote_picker: false
        error: "获取目录列表失败，请重试"

  on_subdir_select: (event) ->
    subdir = event.target.value
    @setState selected_subdir: subdir
    return unless subdir

    uid        = @props.uid
    column_key = @props.column_key
    ar_url     = @get_ar_url()

    @setState fetching: true, error: ""

    form_data = new FormData()
    form_data.append("analysis_uid",   uid)
    form_data.append("subdir",         subdir)
    form_data.append("field_keyword",  column_key)
    form_data.append("_authenticator", @get_authenticator())

    fetch ar_url + "/@@fetch-all-remote-files",
      method: "POST"
      credentials: "include"
      body: form_data
    .then (r) -> r.json()
    .then (resp) =>
      if resp.success
        succeeded = (resp.files or []).filter (f) -> f.success
        new_state =
          fetching: false
          show_remote_picker: false
          subdirs: []
          selected_subdir: ""
          error: ""
          batch_files: succeeded
          batch_count: succeeded.length
        if resp.first_uid
          new_state.filename     = resp.first_filename
          new_state.download_url = resp.first_download_url
          if @props.update_editable_field?
            @props.update_editable_field uid, column_key, resp.first_uid, @props.item
          if @props.save_editable_field?
            @props.save_editable_field uid, column_key, resp.first_uid, @props.item
        @setState new_state
      else
        @setState fetching: false, error: resp.error or "批量获取失败"
    .catch (e) =>
      console.error "[FileField] fetch-all-remote-files error", e
      @setState fetching: false, error: "批量获取失败，请重试"

  on_remote_picker_close: ->
    @setState
      show_remote_picker: false
      subdirs: []
      selected_subdir: ""

  render: ->
    {filename, download_url, error,
     show_remote_picker, subdirs, selected_subdir,
     remote_loading, fetching, batch_files} = @state

    busy     = fetching
    is_batch = batch_files and batch_files.length > 0

    btn_style =
      padding: "2px 10px"
      fontSize: "12px"
      borderRadius: "3px"
      display: "inline-block"
      lineHeight: "1.5"
      cursor: "pointer"

    small_btn_style =
      padding: "2px 8px"
      fontSize: "12px"
      borderRadius: "3px"
      display: "inline-block"
      lineHeight: "1.5"
      cursor: "pointer"

    batch_file_nodes = if is_batch and not busy
      batch_files.map (f, i) ->
        React.createElement "a",
          key: i
          href: f.download_url
          target: "_blank"
          style: {fontSize: "12px", color: "#337ab7", display: "block",
                  overflow: "hidden", textOverflow: "ellipsis",
                  whiteSpace: "nowrap", maxWidth: "200px"}
          "📎 " + f.filename
    else
      null

    batch_section = if batch_file_nodes
      React.createElement "span",
        style: {display: "flex", flexDirection: "column", gap: "2px", marginTop: "2px"}
        batch_file_nodes
    else
      null

    <span className="lp-file-field"
          style={{display: "inline-flex", flexDirection: "column", gap: "4px"}}>

      <span style={{display: "inline-flex", alignItems: "center", gap: "6px"}}>

        {busy and
          <span className="btn btn-xs btn-default" disabled>
            {"获取中..."}
          </span>
        }

        {not busy and not show_remote_picker and
          <span className="btn btn-xs btn-default"
                style={btn_style}
                onClick={@on_remote_picker_open}>
            {if filename or is_batch then "重新获取" else "从服务器获取"}
          </span>
        }

        {filename and not busy and not is_batch and
          <a href={download_url}
             target="_blank"
             style={{fontSize: "12px", color: "#337ab7", maxWidth: "150px",
                     overflow: "hidden", textOverflow: "ellipsis",
                     whiteSpace: "nowrap", display: "inline-block",
                     verticalAlign: "middle"}}>
            {"📎 " + filename}
          </a>
        }

        {error and
          <span style={{color: "red", fontSize: "12px"}}>{error}</span>
        }

      </span>

      {batch_section}

      {show_remote_picker and
        <span style={{display: "inline-flex", alignItems: "center", gap: "6px"}}>

          {remote_loading and
            <span style={{fontSize: "12px", color: "#888"}}>{"加载中..."}</span>
          }

          {fetching and
            <span style={{fontSize: "12px", color: "#888"}}>{"正在获取全部文件..."}</span>
          }

          {not remote_loading and not fetching and subdirs.length > 0 and
            <select
              value={selected_subdir}
              onChange={@on_subdir_select}
              style={{fontSize: "12px", padding: "2px 4px", maxWidth: "180px"}}>
              <option value="">{"-- 选择样本目录（自动获取全部）--"}</option>
              {subdirs.map (d, i) ->
                <option key={i} value={d}>{d}</option>
              }
            </select>
          }

          {not remote_loading and not fetching and subdirs.length is 0 and
            <span style={{fontSize: "12px", color: "#888"}}>{"暂无目录"}</span>
          }

          {not fetching and
            <span className="btn btn-xs btn-default"
                  style={small_btn_style}
                  onClick={@on_remote_picker_close}>
              {"取消"}
            </span>
          }

        </span>
      }

    </span>

export default FileField
