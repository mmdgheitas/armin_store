/* ==========================================================================
   اسکریپت پنل مدیریت — وانیلا JS
   ========================================================================== */
(function () {
  "use strict";

  var $ = function (sel, root) {
    return (root || document).querySelector(sel);
  };
  var $$ = function (sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  };

  /* --- سایدبار موبایل --- */
  function initSidebar() {
    var toggle = $("[data-sidebar-toggle]");
    var sidebar = $(".admin-sidebar");
    if (!toggle || !sidebar) return;
    toggle.addEventListener("click", function (e) {
      e.stopPropagation();
      sidebar.classList.toggle("open");
    });
    document.addEventListener("click", function (e) {
      if (
        sidebar.classList.contains("open") &&
        !sidebar.contains(e.target) &&
        e.target !== toggle
      ) {
        sidebar.classList.remove("open");
      }
    });
  }

  /* --- تأیید عملیات --- */
  function initConfirm() {
    document.addEventListener("click", function (e) {
      var el = e.target.closest("[data-confirm]");
      if (!el) return;
      var message = el.getAttribute("data-confirm") || "آیا از انجام این عملیات مطمئن هستید؟";
      if (!window.confirm(message)) {
        e.preventDefault();
        e.stopPropagation();
      }
    });
  }

  /* --- ردیف‌های پویا (تنوع محصول / اقلام سفارش) --- */
  function initDynamicRows() {
    $$("[data-dynamic-rows]").forEach(function (wrapper) {
      var container = $("[data-rows-container]", wrapper);
      var template = $("template[data-row-template]", wrapper);
      var addBtn = $("[data-add-row]", wrapper);
      if (!container || !template) return;

      var reindex = function () {
        $$("[data-row]", container).forEach(function (row, index) {
          $$("input, select, textarea", row).forEach(function (field) {
            if (field.name) {
              field.name = field.name.replace(/__INDEX__|\[\d+\]/g, "[" + index + "]").replace(/__IDX__/g, index);
            }
          });
        });
      };

      if (addBtn) {
        addBtn.addEventListener("click", function () {
          var html = template.innerHTML.replace(/__INDEX__/g, String(Date.now()));
          var temp = document.createElement("div");
          temp.innerHTML = html.trim();
          var node = temp.firstElementChild;
          container.appendChild(node);
          reindex();
        });
      }

      container.addEventListener("click", function (e) {
        var remove = e.target.closest("[data-remove-row]");
        if (!remove) return;
        e.preventDefault();
        var row = remove.closest("[data-row]");
        if (row) row.remove();
        reindex();
      });
    });
  }

  /* --- پیش‌نمایش تصویر --- */
  function initPreviews() {
    $$("input[type='file'][data-preview]").forEach(function (input) {
      input.addEventListener("change", function () {
        var target = $(input.getAttribute("data-preview"));
        if (!target || !input.files || !input.files[0]) return;
        var reader = new FileReader();
        reader.onload = function (e) {
          target.src = e.target.result;
          target.style.display = "";
        };
        reader.readAsDataURL(input.files[0]);
      });
    });
  }

  /* --- نمودار خطی سبک --- */
  function drawChart(canvas) {
    var raw = canvas.getAttribute("data-points");
    if (!raw) return;
    var points;
    try {
      points = JSON.parse(raw);
    } catch (e) {
      return;
    }
    if (!points.length) return;
    points = points.map(function (p) {
      return {
        label: p.label !== undefined ? p.label : p.key,
        value: p.value !== undefined ? p.value : (p.count || 0),
      };
    });

    var dpr = window.devicePixelRatio || 1;
    var rect = canvas.getBoundingClientRect();
    var width = Math.max(rect.width, 320);
    var height = parseInt(canvas.getAttribute("data-height") || "240", 10);
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.height = height + "px";
    var ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);

    var pad = { top: 18, right: 14, bottom: 30, left: 14 };
    var chartW = width - pad.left - pad.right;
    var chartH = height - pad.top - pad.bottom;
    var max = Math.max.apply(
      null,
      points.map(function (p) {
        return p.value;
      })
    );
    max = max > 0 ? max * 1.15 : 1;

    ctx.clearRect(0, 0, width, height);

    // خطوط شبکه
    ctx.strokeStyle = "#eef2f7";
    ctx.lineWidth = 1;
    for (var i = 0; i <= 4; i += 1) {
      var y = pad.top + (chartH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(width - pad.right, y);
      ctx.stroke();
    }

    var stepX = points.length > 1 ? chartW / (points.length - 1) : chartW;
    var coords = points.map(function (p, index) {
      return {
        x: pad.left + stepX * index,
        y: pad.top + chartH - (p.value / max) * chartH,
        label: p.label,
        value: p.value,
      };
    });

    // ناحیه زیر نمودار
    var grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + chartH);
    grad.addColorStop(0, "rgba(15,118,110,0.22)");
    grad.addColorStop(1, "rgba(15,118,110,0.01)");
    ctx.beginPath();
    ctx.moveTo(coords[0].x, pad.top + chartH);
    coords.forEach(function (p) {
      ctx.lineTo(p.x, p.y);
    });
    ctx.lineTo(coords[coords.length - 1].x, pad.top + chartH);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // خط
    ctx.beginPath();
    ctx.strokeStyle = "#0f766e";
    ctx.lineWidth = 2.4;
    ctx.lineJoin = "round";
    coords.forEach(function (p, index) {
      if (index === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();

    // نقاط
    coords.forEach(function (p) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 3.4, 0, Math.PI * 2);
      ctx.fillStyle = "#fff";
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#0f766e";
      ctx.stroke();
    });

    // برچسب‌ها
    ctx.fillStyle = "#94a3b8";
    ctx.font = "11px Vazirmatn, Tahoma, sans-serif";
    ctx.textAlign = "center";
    var every = Math.ceil(points.length / 8);
    coords.forEach(function (p, index) {
      if (index % every !== 0 && index !== points.length - 1) return;
      ctx.fillText(p.label, p.x, height - 8);
    });
  }

  function initCharts() {
    $$("canvas[data-chart]").forEach(drawChart);
    var timer = null;
    window.addEventListener("resize", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        $$("canvas[data-chart]").forEach(drawChart);
      }, 200);
    });
  }

  /* --- تب‌های تنظیمات --- */
  function initTabs() {
    $$("[data-admin-tabs]").forEach(function (wrap) {
      var buttons = $$("[data-tab-target]", wrap);
      buttons.forEach(function (btn) {
        btn.addEventListener("click", function () {
          buttons.forEach(function (b) {
            b.classList.toggle("active", b === btn);
          });
          $$("[data-tab-panel]").forEach(function (panel) {
            panel.classList.toggle("hidden", panel.getAttribute("data-tab-panel") !== btn.getAttribute("data-tab-target"));
          });
        });
      });
    });
  }

  /* --- ارسال دوباره محافظت --- */
  function initSubmitGuard() {
    $$("form").forEach(function (form) {
      if (form.hasAttribute("data-no-guard")) return;
      form.addEventListener("submit", function () {
        var btn = form.querySelector('button[type="submit"]');
        if (btn) {
          setTimeout(function () {
            btn.disabled = true;
            btn.classList.add("disabled");
          }, 10);
        }
      });
    });
  }

  /* --- کپی متن --- */
  function initCopy() {
    $$("[data-copy]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var text = btn.getAttribute("data-copy");
        var done = function () {
          btn.textContent = "کپی شد";
          setTimeout(function () {
            btn.textContent = "کپی";
          }, 1600);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done);
        } else {
          var area = document.createElement("textarea");
          area.value = text;
          document.body.appendChild(area);
          area.select();
          document.execCommand("copy");
          area.remove();
          done();
        }
      });
    });
  }

  /* --- انتخاب همه --- */
  function initCheckAll() {
    $$("[data-check-all]").forEach(function (master) {
      master.addEventListener("change", function () {
        var name = master.getAttribute("data-check-all");
        $$('input[name="' + name + '"]').forEach(function (box) {
          box.checked = master.checked;
        });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initSidebar();
    initConfirm();
    initDynamicRows();
    initPreviews();
    initCharts();
    initTabs();
    initSubmitGuard();
    initCopy();
    initCheckAll();
  });

  window.adminRedrawCharts = function () {
    $$("canvas[data-chart]").forEach(drawChart);
  };
})();
