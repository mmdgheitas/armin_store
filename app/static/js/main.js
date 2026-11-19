/* ==========================================================================
   اسکریپت فروشگاه — وانیلا JS، بدون وابستگی خارجی
   ========================================================================== */
(function () {
  "use strict";

  var $ = function (sel, root) {
    return (root || document).querySelector(sel);
  };
  var $$ = function (sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  };

  var csrfToken = function () {
    var meta = $('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  };

  /* --- اعلان شناور --- */
  window.showToast = function (message, type) {
    var stack = $(".toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "toast-stack";
      document.body.appendChild(stack);
    }
    var el = document.createElement("div");
    el.className = "toast " + (type || "");
    el.setAttribute("role", "status");
    el.textContent = message;
    stack.appendChild(el);
    setTimeout(function () {
      el.style.opacity = "0";
      el.style.transition = "opacity .3s";
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 320);
    }, 3800);
  };

  /* --- درخواست JSON --- */
  window.postJSON = function (url, payload) {
    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken(),
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify(payload || {}),
      credentials: "same-origin",
    }).then(function (response) {
      return response.json().then(function (data) {
        data.__status = response.status;
        return data;
      });
    });
  };

  /* --- شمارنده سبد --- */
  function updateCartBadge(count) {
    $$("[data-cart-count]").forEach(function (el) {
      if (typeof count === "number") {
        el.textContent = count;
        el.classList.toggle("d-none", count <= 0);
      }
    });
  }

  /* --- منوی موبایل --- */
  function initMobileMenu() {
    var menu = $("#mobile-menu");
    var openBtn = $("[data-menu-open]");
    var closeBtn = $("[data-menu-close]");
    if (!menu || !openBtn) return;
    var open = function () {
      menu.classList.add("open");
      menu.setAttribute("aria-hidden", "false");
      document.body.style.overflow = "hidden";
    };
    var close = function () {
      menu.classList.remove("open");
      menu.setAttribute("aria-hidden", "true");
      document.body.style.overflow = "";
    };
    openBtn.addEventListener("click", open);
    if (closeBtn) closeBtn.addEventListener("click", close);
    menu.addEventListener("click", function (e) {
      if (e.target === menu) close();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
    });
  }

  /* --- بستن خودکار پیام‌ها --- */
  function initFlashes() {
    $$(".flash-wrap .alert").forEach(function (el) {
      var btn = el.querySelector("[data-dismiss]");
      if (btn) {
        btn.addEventListener("click", function () {
          el.remove();
        });
      }
      setTimeout(function () {
        el.style.transition = "opacity .4s";
        el.style.opacity = "0";
        setTimeout(function () {
          if (el.parentNode) el.remove();
        }, 420);
      }, 6500);
    });
  }

  /* --- تعداد --- */
  function initQty() {
    $$("[data-qty]").forEach(function (box) {
      var input = box.querySelector("input");
      var minus = box.querySelector("[data-qty-minus]");
      var plus = box.querySelector("[data-qty-plus]");
      if (!input) return;
      var min = parseInt(input.getAttribute("min") || "1", 10);
      var max = parseInt(input.getAttribute("max") || "999", 10);
      var clamp = function (v) {
        v = isNaN(v) ? min : v;
        return Math.max(min, Math.min(max, v));
      };
      if (minus) {
        minus.addEventListener("click", function () {
          input.value = clamp(parseInt(input.value, 10) - 1);
          input.dispatchEvent(new Event("change", { bubbles: true }));
        });
      }
      if (plus) {
        plus.addEventListener("click", function () {
          input.value = clamp(parseInt(input.value, 10) + 1);
          input.dispatchEvent(new Event("change", { bubbles: true }));
        });
      }
      input.addEventListener("change", function () {
        input.value = clamp(parseInt(input.value, 10));
      });
    });
  }

  /* --- افزودن به سبد (AJAX) --- */
  function initCartForms() {
    $$("form[data-cart-form]").forEach(function (form) {
      form.addEventListener("submit", function (e) {
        if (!window.fetch) return;
        e.preventDefault();
        var btn = form.querySelector('button[type="submit"]');
        var original = btn ? btn.innerHTML : "";
        if (btn) {
          btn.disabled = true;
          btn.innerHTML = '<span class="spinner"></span>';
        }
        var data = {
          variant_id: (form.querySelector('[name="variant_id"]') || {}).value || form.getAttribute("data-variant-id"),
          quantity: (form.querySelector('[name="quantity"]') || {}).value || 1,
        };
        window
          .postJSON("/api/cart/add", data)
          .then(function (res) {
            if (res.ok) {
              window.showToast(res.message || "به سبد خرید اضافه شد.", "success");
              if (res.cart) updateCartBadge(res.cart.count);
            } else {
              window.showToast(res.message || "خطا در افزودن به سبد.", "error");
            }
          })
          .catch(function () {
            window.showToast("ارتباط با سرور برقرار نشد.", "error");
          })
          .finally(function () {
            if (btn) {
              btn.disabled = false;
              btn.innerHTML = original;
            }
          });
      });
    });
  }

  /* --- علاقه‌مندی و مقایسه --- */
  function initToggles() {
    $$("[data-wishlist]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.getAttribute("data-wishlist");
        window
          .postJSON("/api/wishlist/toggle", { product_id: id })
          .then(function (res) {
            if (res.ok) {
              btn.classList.toggle("active", !!res.active);
              var icon = btn.querySelector("[data-wish-icon]");
              if (icon) icon.textContent = res.active ? "♥" : "♡";
              var label = btn.querySelector("[data-wish-label]");
              if (label) label.textContent = res.active ? "در علاقه‌مندی‌ها" : "افزودن به علاقه‌مندی‌ها";
              window.showToast(res.message, "success");
            } else if (res.__status === 401) {
              window.location.href = "/auth/login?next=" + encodeURIComponent(location.pathname);
            } else {
              window.showToast(res.message || "خطا", "error");
            }
          })
          .catch(function () {
            window.showToast("ارتباط با سرور برقرار نشد.", "error");
          });
      });
    });

    $$("[data-compare]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.getAttribute("data-compare");
        window
          .postJSON("/api/compare/toggle", { product_id: id })
          .then(function (res) {
            if (res.ok) {
              btn.classList.toggle("active", !!res.active);
              window.showToast(res.message, "success");
            } else {
              window.showToast(res.message || "خطا", "error");
            }
          })
          .catch(function () {
            window.showToast("ارتباط با سرور برقرار نشد.", "error");
          });
      });
    });
  }

  /* --- انتخاب تنوع محصول --- */
  function formatPrice(value) {
    var num = parseInt(value, 10) || 0;
    return num.toLocaleString("fa-IR");
  }

  function initVariants() {
    var root = $("[data-product-form]");
    if (!root) return;
    var hidden = root.querySelector('[name="variant_id"]');
    var priceEl = root.querySelector("[data-price-label]");
    var oldEl = root.querySelector("[data-old-price]");
    var stockEl = root.querySelector("[data-stock-note]");
    var qtyInput = root.querySelector('[name="quantity"]');
    var addBtn = root.querySelector('[data-add-btn]');
    var skuEl = root.querySelector("[data-sku-label]");
    var mainImage = root.querySelector("[data-gallery-main] img");

    var select = function (btn) {
      $$(".variant-option", root).forEach(function (b) {
        b.classList.toggle("active", b === btn);
      });
      if (hidden) hidden.value = btn.getAttribute("data-variant-id");
      var price = btn.getAttribute("data-price");
      var compare = btn.getAttribute("data-compare-price");
      var stock = parseInt(btn.getAttribute("data-stock") || "0", 10);
      var sku = btn.getAttribute("data-sku");
      if (priceEl) {
        priceEl.innerHTML = formatPrice(price) + ' <small>تومان</small>';
      }
      if (oldEl) {
        if (compare && parseInt(compare, 10) > parseInt(price, 10)) {
          oldEl.textContent = formatPrice(compare) + " تومان";
          oldEl.style.display = "";
        } else {
          oldEl.style.display = "none";
        }
      }
      if (stockEl) {
        var cls = stock <= 0 ? "out-of-stock" : stock <= 3 ? "low-stock" : "in-stock";
        stockEl.className = "stock-note " + cls;
        stockEl.innerHTML =
          '<span class="stock-dot"></span>' +
          (stock <= 0
            ? "ناموجود"
            : stock <= 3
            ? "تنها " + formatPrice(stock) + " عدد در انبار"
            : "موجود در انبار");
      }
      if (qtyInput) {
        qtyInput.setAttribute("max", String(Math.max(1, stock)));
        if (parseInt(qtyInput.value, 10) > stock) qtyInput.value = Math.max(1, stock);
      }
      if (addBtn) {
        addBtn.disabled = stock <= 0;
        addBtn.textContent = stock <= 0 ? "ناموجود" : "افزودن به سبد خرید";
      }
      if (skuEl && sku) skuEl.textContent = sku;
      var image = btn.getAttribute("data-image");
      if (mainImage && image) mainImage.src = image;
      var url = new URL(window.location.href);
      url.searchParams.set("variant", btn.getAttribute("data-variant-id"));
      window.history.replaceState({}, "", url.toString());
    };

    $$(".variant-option", root).forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (btn.disabled) return;
        select(btn);
      });
    });
  }

  /* --- گالری --- */
  function initGallery() {
    $$("[data-gallery-main]").forEach(function (wrap) {
      var img = wrap.querySelector("img");
      var thumbs = $$("[data-gallery-thumb]");
      if (!img) return;
      thumbs.forEach(function (thumb) {
        thumb.addEventListener("click", function () {
          var src = thumb.getAttribute("data-src");
          if (src) img.src = src;
          thumbs.forEach(function (t) {
            t.classList.toggle("active", t === thumb);
          });
        });
      });
    });
  }

  /* --- تب‌ها --- */
  function initTabs() {
    $$("[data-tabs]").forEach(function (wrap) {
      var buttons = $$("[data-tab]", wrap).filter(function (b) {
        return b.tagName === "BUTTON";
      });
      buttons.forEach(function (btn) {
        btn.addEventListener("click", function () {
          var target = btn.getAttribute("data-tab");
          buttons.forEach(function (b) {
            b.classList.toggle("active", b === btn);
          });
          $$(".tab-panel", wrap.parentNode).forEach(function (panel) {
            panel.classList.toggle("active", panel.getAttribute("data-panel") === target);
          });
        });
      });
    });
  }

  /* --- امتیاز ستاره‌ای --- */
  function initRating() {
    $$("[data-rating-input]").forEach(function (wrap) {
      var input = wrap.querySelector('input[type="hidden"]');
      var stars = $$("[data-star]", wrap);
      var paint = function (value) {
        stars.forEach(function (star) {
          star.textContent = parseInt(star.getAttribute("data-star"), 10) <= value ? "★" : "☆";
        });
      };
      stars.forEach(function (star) {
        star.addEventListener("mouseenter", function () {
          paint(parseInt(star.getAttribute("data-star"), 10));
        });
        star.addEventListener("click", function () {
          var v = parseInt(star.getAttribute("data-star"), 10);
          if (input) input.value = v;
          paint(v);
        });
      });
      wrap.addEventListener("mouseleave", function () {
        paint(input ? parseInt(input.value, 10) || 0 : 0);
      });
      paint(input ? parseInt(input.value, 10) || 0 : 0);
    });
  }

  /* --- کد یکبار مصرف --- */
  function initOtp() {
    $$("[data-otp-group]").forEach(function (group) {
      var inputs = $$("input", group);
      inputs.forEach(function (input, index) {
        input.addEventListener("input", function () {
          input.value = input.value.replace(/[^0-9]/g, "").slice(-1);
          if (input.value && inputs[index + 1]) inputs[index + 1].focus();
        });
        input.addEventListener("keydown", function (e) {
          if (e.key === "Backspace" && !input.value && inputs[index - 1]) {
            inputs[index - 1].focus();
          }
        });
        input.addEventListener("paste", function (e) {
          var text = (e.clipboardData || window.clipboardData).getData("text").replace(/[^0-9]/g, "");
          if (!text) return;
          e.preventDefault();
          inputs.forEach(function (inp, i) {
            inp.value = text[i] || "";
          });
          var last = Math.min(text.length, inputs.length) - 1;
          if (inputs[last]) inputs[last].focus();
        });
      });
      var form = group.closest("form");
      if (form) {
        form.addEventListener("submit", function () {
          var hidden = form.querySelector('input[name="code"]');
          if (!hidden) {
            hidden = document.createElement("input");
            hidden.type = "hidden";
            hidden.name = "code";
            form.appendChild(hidden);
          }
          hidden.value = inputs
            .map(function (i) {
              return i.value;
            })
            .join("");
        });
      }
    });
  }

  /* --- تأیید عملیات --- */
  function initConfirm() {
    document.addEventListener("click", function (e) {
      var el = e.target.closest("[data-confirm]");
      if (!el) return;
      var message = el.getAttribute("data-confirm") || "آیا مطمئن هستید؟";
      if (!window.confirm(message)) {
        e.preventDefault();
        e.stopPropagation();
      }
    });
  }

  /* --- جلوگیری از ارسال دوباره فرم --- */
  function initSubmitGuard() {
    $$("form").forEach(function (form) {
      if (form.hasAttribute("data-no-guard")) return;
      form.addEventListener("submit", function () {
        var btn = form.querySelector('button[type="submit"]');
        if (btn && !btn.disabled) {
          setTimeout(function () {
            btn.disabled = true;
          }, 10);
        }
      });
    });
  }

  /* --- هزینه ارسال (نمایشی) --- */
  function initShippingPreview() {
    var radios = $$('input[name="shipping_method_id"]');
    var costEl = $("[data-total-cost]");
    var shipEl = $("[data-ship-cost]");
    if (!radios.length || !costEl) return;
    var subtotal = parseInt(costEl.getAttribute("data-subtotal") || "0", 10);
    var discount = parseInt(costEl.getAttribute("data-discount") || "0", 10);
    radios.forEach(function (radio) {
      radio.addEventListener("change", function () {
        var cost = parseInt(radio.getAttribute("data-cost") || "0", 10);
        var freeAbove = parseInt(radio.getAttribute("data-free-above") || "0", 10);
        if (freeAbove > 0 && subtotal - discount >= freeAbove) cost = 0;
        if (shipEl) shipEl.textContent = cost > 0 ? formatPrice(cost) + " تومان" : "رایگان";
        costEl.textContent = formatPrice(subtotal - discount + cost) + " تومان";
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initMobileMenu();
    initFlashes();
    initQty();
    initCartForms();
    initToggles();
    initVariants();
    initGallery();
    initTabs();
    initRating();
    initOtp();
    initConfirm();
    initSubmitGuard();
    initShippingPreview();
  });
})();
