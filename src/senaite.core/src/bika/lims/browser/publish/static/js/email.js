(function() {
  /* Please use this command to compile this file into the parent `js` directory:
      coffee --no-header -w -o ../js -c email.coffee
  */
  var EmailController, RecipientPicker;

  // DOCUMENT READY ENTRY POINT
  document.addEventListener("DOMContentLoaded", function() {
    var controller;
    console.debug("*** Loading Email Controller");
    controller = new EmailController();
    return controller.initialize();
  });

  RecipientPicker = class RecipientPicker {
    constructor(opts) {
      this.baseUrl = opts.baseUrl; // 例如 /TCRx/clients/client-1/email
      this.$input = document.querySelector(opts.inputSelector);
      this.$list = document.querySelector(opts.suggestionsSelector);
      this.$chips = document.querySelector(opts.chipsSelector);
      this.$hidden = document.querySelector(opts.hiddenSelector);
      this.onChange = typeof opts.onChange === "function" ? opts.onChange : function(){};
      this.items = [];        // 已选 [{address,name,email}]
      this.suggestions = [];  // 当前下拉
      this.activeIndex = -1;  // 下拉高亮索引
      this.debounceTimer = null;
      this.bind();
    }

    exist(){ return !!(this.$input && this.$list && this.$chips && this.$hidden); }
    isListVisible(){ return this.$list && this.$list.style.display !== 'none' && this.$list.children.length > 0; }
    isValidEmail(s){ return /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/.test((s||'').trim()); }
    hideList(){ this.$list.style.display = 'none'; }

    renderActive() {
      Array.from(this.$list.children).forEach((el, idx) => {
        if (idx === this.activeIndex) {
          el.classList.add('active');
          el.style.background = '#e6f2ff';
        } else {
          el.classList.remove('active');
          el.style.background = '';
        }
      });
    }

    showList(items) {
      if (!this.exist()) return;
      this.suggestions = items || [];
      this.activeIndex = this.suggestions.length ? 0 : -1;
      this.$list.innerHTML = '';
      this.suggestions.forEach((it, i) => {
        const a = document.createElement('a');
        a.className = 'dropdown-item';
        a.href = 'javascript:void(0)';
        a.style.display = 'block';
        a.style.padding = '6px 10px';
        a.textContent = it.address;
        a.addEventListener('mouseover', () => { this.activeIndex = i; this.renderActive(); });
        a.addEventListener('click', () => { this.choose(i); this.hideList(); this.$input.value = ''; });
        this.$list.appendChild(a);
      });
      this.$list.style.display = this.suggestions.length ? 'block' : 'none';
      this.renderActive();
    }

    search(term) {
      const fd = new FormData();
      fd.append('term', term);
      fd.append('limit', '100');
      // 命中 EmailView 的 ajax 路由：/email/user_emails
      fetch(this.baseUrl + '/user_emails', { method: 'POST', body: fd, credentials: 'include' })
        .then(r => r.json())
        .then(j => { if (j && j.ok) this.showList(j.items || []); else this.showList([]); })
        .catch(() => this.showList([]));
    }

    bind() {
      if (!this.exist()) return;

      // 输入（带中文合成态处理）
      this.$input.addEventListener('input', (e) => {
        if (e.isComposing) return;
        const term = (e.target.value || '').trim();
        clearTimeout(this.debounceTimer);
        this.debounceTimer = setTimeout(() => { term ? this.search(term) : this.hideList(); }, 200);
      });
      this.$input.addEventListener('compositionend', (e) => {
        const term = (e.target.value || '').trim(); if (term) this.search(term);
      });

      // 键盘：上下选择 + 回车确认；无候选时，回车添加合法邮箱
      this.$input.addEventListener('keydown', (e) => {
        if (e.isComposing) return;
        const hasList = this.isListVisible() && this.suggestions.length > 0;
        if (e.key === 'ArrowDown' && hasList) { e.preventDefault(); this.activeIndex = (this.activeIndex + 1) % this.suggestions.length; this.renderActive(); return; }
        if (e.key === 'ArrowUp' && hasList)   { e.preventDefault(); this.activeIndex = (this.activeIndex - 1 + this.suggestions.length) % this.suggestions.length; this.renderActive(); return; }
        if (e.key === 'Enter') {
          if (hasList) {
            e.preventDefault(); const idx = this.activeIndex >= 0 ? this.activeIndex : 0; this.choose(idx); this.hideList(); this.$input.value = ''; return;
          }
          const val = (this.$input.value || '').trim();
          if (val && this.isValidEmail(val)) { e.preventDefault(); this.addFree(val); this.$input.value = ''; }
        }
      });

      document.addEventListener('click', (e) => {
        if (!this.$list.contains(e.target) && e.target !== this.$input) this.hideList();
      });
    }

    choose(index) { if (index < 0 || index >= this.suggestions.length) return; this.add(this.suggestions[index]); }

    addFree(text) {
      // 支持 "Name <email>" 或纯邮箱
      let address = (text || '').trim();
      let email = address;
      const m = address.match(/<([^>]+)>/);
      if (m) email = (m[1] || '').trim();
      if (!this.isValidEmail(email)) return;
      let name = '';
      const m2 = address.match(/^(.+?)\s*<[^>]+>$/);
      if (m2) name = (m2[1] || '').trim();
      this.add({ address: address, name: name, email: email });
    }

    add(it) {
      const key = (it.email || '').toLowerCase();
      if (!key) return;
      if (this.items.some(x => (x.email || '').toLowerCase() === key)) return; // 去重

      this.items.push(it);

      // 追加 chip
      const chip = document.createElement('span');
      chip.className = 'badge badge-primary';
      chip.style.cssText = 'padding:6px 10px; font-weight:500;';
      chip.textContent = it.address;
      const close = document.createElement('span');
      close.textContent = ' ×';
      close.style.cursor = 'pointer';
      close.style.marginLeft = '6px';
      chip.appendChild(close);
      this.$chips.appendChild(chip);

      // 追加隐藏域 -> 提交时后端读取 recipients:list
      const hidden = document.createElement('input');
      hidden.type = 'hidden';
      hidden.name = 'recipients:list';
      hidden.value = it.address;
      this.$hidden.appendChild(hidden);

      close.addEventListener('click', () => { this.remove(it.email, chip, hidden); });
      this.onChange(this.items.length);
    }

    remove(email, chipEl, hiddenEl) {
      const key = (email || '').toLowerCase();
      this.items = this.items.filter(x => (x.email || '').toLowerCase() !== key);
      if (chipEl && chipEl.parentNode) chipEl.parentNode.removeChild(chipEl);
      if (hiddenEl && hiddenEl.parentNode) hiddenEl.parentNode.removeChild(hiddenEl);
      // 保险：清理同邮箱的所有隐藏域
      Array.from(this.$hidden.querySelectorAll('input[name="recipients:list"]')).forEach(inp => {
        const v = (inp.value || '').toLowerCase();
        const m = v.match(/<([^>]+)>/); const e = m ? (m[1] || '').toLowerCase() : v;
        if (e === key) inp.remove();
      });
      this.onChange(this.items.length);
    }
  };

  // ------------------------------
  // 原有控制器（在此基础上最小改造）
  // ------------------------------
  EmailController = class EmailController {
    constructor() {
      this.bind_eventhandler = this.bind_eventhandler.bind(this);
      this.toggle_attachments_container = this.toggle_attachments_container.bind(this);
      this.on_add_attachments_click = this.on_add_attachments_click.bind(this);
      this.on_attachments_select = this.on_attachments_select.bind(this);
      this.on_change_select_all_attachments = this.on_change_select_all_attachments.bind(this);

      this.update_send_button_state = this.update_send_button_state.bind(this);
      this.init_recipient_picker = this.init_recipient_picker.bind(this);
      this.sizeLimitExceeded = false; // 当前是否超出大小限制
      this.recipientPicker = null;

      this.bind_eventhandler();
      return this;
    }

    initialize() {
      console.debug("senaite.core:Email::initialize");
      // Initialize overlays
      this.init_overlays();
      this.init_recipient_picker();
      this.update_send_button_state();
      return;
    }

    init_overlays() {
      /*
       * Initialize all overlays for later loading
       *
       */
      console.debug("senaite.core:Email::init_overlays");
      return $("a.attachment-link,a.report-link").prepOverlay({
        subtype: "iframe",
        config: {
          closeOnClick: true,
          closeOnEsc: true,
          onLoad: function(event) {
            var iframe, overlay;
            overlay = this.getOverlay();
            iframe = overlay.find("iframe");
            return iframe.css({
              "background": "white"
            });
          }
        }
      });
    }

    bind_eventhandler() {
      /*
       * Binds callbacks on elements
       *
       * N.B. We attach all the events to the body and refine the selector to
       * delegate the event: https://learn.jquery.com/events/event-delegation/
       *
       */
      console.debug("senaite.core::bind_eventhandler");
      // Toggle additional attachments visibility
      $("body").on("click", "#add-attachments", this.on_add_attachments_click);
      // Select/deselect additional attachments
      $("body").on("change", ".attachment input[type='checkbox']", this.on_attachments_select);
      // Select/deselect all additional attachments
      return $("body").on("change", "#select-all-attachments", this.on_change_select_all_attachments);
    }

    init_recipient_picker() {
      const inputEl = document.querySelector('#recipient-input');
      if (!inputEl) return; // 模板未放自选控件则忽略

      this.recipientPicker = new RecipientPicker({
        baseUrl: this.get_base_url(), // => /email
        inputSelector: '#recipient-input',
        suggestionsSelector: '#recipient-suggestions',
        chipsSelector: '#recipient-chips',
        hiddenSelector: '#recipient-hidden',
        onChange: () => { this.update_send_button_state(); }
      });
    }

    update_send_button_state() {
      const $send = $("input[name='send']");
      if ($send.length === 0) return;

      const selected = document.querySelectorAll('#recipient-hidden input[name="recipients:list"]').length;
      if (selected === 0) {
        $send.prop("disabled", true);
        return;
      }
      if (!this.sizeLimitExceeded) {
        $send.prop("disabled", false);
      }
    }

    get_base_url() {
      /*
       * Calculate the current base url
       */
      return document.URL.split("?")[0];
    }

    get_api_url(endpoint) {
      /*
       * Build API URL for the given endpoint
       * @param {string} endpoint
       * @returns {string}
       */
      var base_url;
      base_url = this.get_base_url();
      return `${base_url}/${endpoint}`;
    }

    ajax_fetch(endpoint, init) {
      /*
       * Call resource on the server
       * @param {string} endpoint
       * @param {object} options
       * @returns {Promise}
       */
      var request, url;
      url = this.get_api_url(endpoint);
      if (init == null) {
        init = {};
      }
      if (init.method == null) {
        init.method = "POST";
      }
      if (init.credentials == null) {
        init.credentials = "include";
      }
      if (init.body == null) {
        init.body = null;
      }
      if (init.header == null) {
        init.header = null;
      }
      console.info(`Email::fetch:endpoint=${endpoint} init=`, init);
      request = new Request(url, init);
      return fetch(request).then(function(response) {
        return response.json();
      });
    }

    is_visible(element) {
      /*
       * Checks if the element is visible
       */
      if ($(element).css("display") === "none") {
        return false;
      }
      return true;
    }

    toggle_attachments_container(toggle = null) {
      /*
       * Toggle the visibility of the attachments container
       */
      var button, container, visible;
      button = $("#add-attachments");
      container = $("#additional-attachments-container");
      visible = this.is_visible(container);
      if (toggle !== null) {
        visible = toggle ? false : true;
      }
      if (visible === true) {
        container.hide();
        return button.text("+");
      } else {
        container.show();
        return button.text("-");
      }
    }

    update_size_info(data) {
      var unit;
      /*
       * Update the total size of the selected attachments
       */
      if (!data) {
        console.warn("No valid size information: ", data);
        return null;
      }
      unit = "kB";
      $("#attachment-files").text(`${data.files}`);

      // 记录状态并更新展示
      this.sizeLimitExceeded = !!data.limit_exceeded;
      if (data.limit_exceeded) {
        $("#email-size").addClass("text-danger");
        $("#email-size").text(`${data.size} ${unit} > ${data.limit} ${unit}`);
      } else {
        $("#email-size").removeClass("text-danger");
        $("#email-size").text(`${data.size} ${unit}`);
      }
      // 统一由 update_send_button_state 决定按钮状态
      this.update_send_button_state();
      return;
    }

    on_add_attachments_click(event) {
      console.debug("°°° Email::on_add_attachments_click");
      event.preventDefault();
      return this.toggle_attachments_container();
    }

    on_attachments_select(event) {
      var count_attachments, form, form_data, init, select_all_checked, select_attachments;
      console.debug("°°° Email::on_attachments_select");
      // extract the form data
      form = $("#send_email_form");
      // form.serialize does not include file attachments
      // form_data = form.serialize()
      form_data = new FormData(form[0]);
      count_attachments = $("input[name='attachment_uids:list']").length;
      select_attachments = form_data.getAll("attachment_uids:list").length;
      select_all_checked = count_attachments === select_attachments;
      $("#select-all-attachments").prop("checked", select_all_checked);
      init = {
        body: form_data
      };
      return this.ajax_fetch("recalculate_size", init).then((data) => {
        return this.update_size_info(data);
      });
    }

    on_change_select_all_attachments(event) {
      var checked;
      console.debug("°°° Email::on_change_select_all_attachments");
      checked = event.target.checked;
      $("input[name='attachment_uids:list']").each(function(index, element) {
        return $(element).prop("checked", checked);
      });
      return this.on_attachments_select();
    }

  };

}).call(this);
