import React from "react"

class FileField extends React.Component

  constructor: (props) ->
    super(props)

    @state =
      uploading: false    # 是否正在上传
      filename: props.filename or ""     # 已上传的文件名
      download_url: props.download_url or ""  # 下载链接
      error: ""           # 错误信息

    @on_file_change = @on_file_change.bind @
    @get_ar_url     = @get_ar_url.bind @

  componentDidUpdate: (prevProps) ->
    # 后端数据更新时同步 state
    prev_filename = prevProps.filename or ""
    curr_filename = @props.filename or ""
    return if prev_filename is curr_filename

    @setState
      filename: curr_filename
      download_url: @props.download_url or ""

  # 从当前页面 URL 解析出 AR 的基础路径
  # 例：/TCRx/clients/client-23/WPB-0258/... -> /TCRx/clients/client-23/WPB-0258
  get_ar_url: ->
    url = window.location.href.split("?")[0].split("#")[0]
    url = url.replace(/\/@@[^\/]*$/, "")
    url

  get_authenticator: ->
    el = document.querySelector("input[name='_authenticator']")
    if el then el.value else ""

  on_file_change: (event) ->
    file = event.target.files?[0]
    return unless file

    uid = @props.uid
    column_key = @props.column_key
    ar_url = @get_ar_url()

    @setState uploading: true, error: ""

    # 构建 FormData
    form_data = new FormData()
    form_data.append("analysis_uid", uid)
    form_data.append("field_keyword", column_key)
    form_data.append("file_upload", file)
    form_data.append("_authenticator", @get_authenticator())

    fetch ar_url + "/@@upload-analysis-file",
      method: "POST"
      credentials: "include"
      body: form_data
    .then (r) -> r.json()
    .then (resp) =>
      if resp.success
        @setState
          uploading: false
          filename: resp.filename
          download_url: resp.download_url
          error: ""

        # 通知 listing 更新字段值（存 Attachment UID）
        if @props.update_editable_field?
          @props.update_editable_field uid, column_key, resp.uid, @props.item
        if @props.save_editable_field?
          @props.save_editable_field uid, column_key, resp.uid, @props.item
      else
        @setState
          uploading: false
          error: resp.error or "上传失败"
    .catch (e) =>
      console.error "[FileField] upload error", e
      @setState
        uploading: false
        error: "上传失败，请重试"

  render: ->
    {filename, download_url, uploading, error} = @state

    <span className="lp-file-field" style={{display: "inline-flex", alignItems: "center", gap: "6px"}}>

      {uploading and
        <span className="btn btn-xs btn-default" disabled>
          {"上传中..."}
        </span>
      }

      {not uploading and
        <label style={{margin: 0, cursor: "pointer"}}>
          <span className="btn btn-xs btn-primary"
                style={{
                  padding: "2px 10px",
                  fontSize: "12px",
                  borderRadius: "3px",
                  border: "1px solid #2e6da4",
                  display: "inline-block",
                  lineHeight: "1.5"
                }}>
            {if filename then "重新上传" else "选择文件"}
          </span>
          <input
            type="file"
            style={{display: "none"}}
            onChange={@on_file_change}
            disabled={@props.disabled} />
        </label>
      }

      {filename and not uploading and
        <a href={download_url}
           target="_blank"
           style={{
             fontSize: "12px",
             color: "#337ab7",
             maxWidth: "120px",
             overflow: "hidden",
             textOverflow: "ellipsis",
             whiteSpace: "nowrap",
             display: "inline-block",
             verticalAlign: "middle"
           }}>
          {"📎 " + filename}
        </a>
      }

      {error and
        <span style={{color: "red", fontSize: "12px"}}>
          {error}
        </span>
      }

    </span>
export default FileField