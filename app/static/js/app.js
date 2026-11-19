(function () {
  "use strict";

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function showToast(message) {
    let container = document.getElementById("toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "toast-container";
      container.className = "toast-container";
      document.body.appendChild(container);
    }
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3400);
  }

  function toPersianDigits(val) {
    return String(val).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[Number(d)]);
  }

  function formatToman(rial) {
    const toman = Math.floor(Number(rial || 0) / 10);
    return toPersianDigits(toman.toLocaleString("en-US")) + " تومان";
  }

  function updateCartBadge(cartData) {
    const badge = document.getElementById("header-cart-badge");
    if (badge && cartData) {
      badge.textContent = cartData.item_count_fa || toPersianDigits(cartData.item_count || 0);
      badge.classList.remove("pop");
      void badge.offsetWidth;
      badge.classList.add("pop");
    }
  }

  // Hero slider
  const slides = document.querySelectorAll(".hero-slide");
  const dots = document.querySelectorAll(".hero-dot");
  if (slides.length > 1) {
    let cur = 0;
    const goTo = (idx) => {
      slides.forEach((s, i) => s.classList.toggle("active", i === idx));
      dots.forEach((d, i) => d.classList.toggle("active", i === idx));
      cur = idx;
    };
    dots.forEach((d, idx) => d.addEventListener("click", () => goTo(idx)));
    setInterval(() => goTo((cur + 1) % slides.length), 6000);
  }

  // AJAX Add to Cart
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-add-cart]");
    if (!btn) return;
    e.preventDefault();
    const variantId = btn.getAttribute("data-variant-id");
    const qtyInput = document.getElementById("pdp-qty-input");
    const qty = qtyInput && btn.hasAttribute("data-from-pdp") ? Number(qtyInput.value || 1) : 1;

    btn.disabled = true;
    try {
      const resp = await fetch("/cart/add", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(),
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({ variant_id: Number(variantId), quantity: qty }),
      });
      const data = await resp.json();
      showToast(data.message || (data.ok ? "به سبد خرید اضافه شد" : "خطا"));
      if (data.cart) updateCartBadge(data.cart);
    } catch {
      showToast("خطا در ارتباط با سرور.");
    } finally {
      btn.disabled = false;
    }
  });

  // Cart Page AJAX Qty +/- and Remove
  async function syncCartPage(url, payload) {
    const resp = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (data.message) showToast(data.message);
    if (data.cart) {
      updateCartBadge(data.cart);
      const subEl = document.getElementById("cart-subtotal");
      const shipEl = document.getElementById("cart-shipping");
      const discEl = document.getElementById("cart-discount");
      const totEl = document.getElementById("cart-total");
      if (subEl) subEl.textContent = data.cart.subtotal_toman_str;
      if (shipEl) shipEl.textContent = data.cart.shipping_fee_str;
      if (discEl) discEl.textContent = data.cart.discount_str;
      if (totEl) totEl.textContent = data.cart.total_toman_str;

      if (data.cart.lines.length === 0) {
        window.location.reload();
        return;
      }
      const lineMap = {};
      data.cart.lines.forEach((l) => { lineMap[l.variant_id] = l; });
      document.querySelectorAll("[data-cart-row]").forEach((row) => {
        const vid = Number(row.getAttribute("data-cart-row"));
        const info = lineMap[vid];
        if (!info) {
          row.remove();
        } else {
          const inp = row.querySelector("[data-cart-qty]");
          const lt = row.querySelector("[data-line-total]");
          if (inp) inp.value = info.quantity;
          if (lt) lt.textContent = info.line_total_str;
        }
      });
    }
  }

  document.addEventListener("click", async (e) => {
    const stepBtn = e.target.closest("[data-qty-step]");
    if (stepBtn) {
      const vid = Number(stepBtn.getAttribute("data-variant-id"));
      const delta = Number(stepBtn.getAttribute("data-qty-step"));
      const row = stepBtn.closest("[data-cart-row]");
      const inp = row ? row.querySelector("[data-cart-qty]") : null;
      const cur = inp ? Number(inp.value) : 1;
      await syncCartPage("/cart/update", { variant_id: vid, quantity: Math.max(0, cur + delta) });
    }
    const remBtn = e.target.closest("[data-cart-remove]");
    if (remBtn) {
      const vid = Number(remBtn.getAttribute("data-cart-remove"));
      await syncCartPage("/cart/remove", { variant_id: vid });
    }
  });

  // Product Detail Variant Switcher
  const variantsJsonEl = document.getElementById("pdp-variants-data");
  if (variantsJsonEl) {
    const variants = JSON.parse(variantsJsonEl.textContent || "[]");
    const pills = document.querySelectorAll("[data-variant-pill]");
    const priceEl = document.getElementById("pdp-price");
    const oldPriceEl = document.getElementById("pdp-compare-price");
    const skuEl = document.getElementById("pdp-sku");
    const stockMsgEl = document.getElementById("pdp-stock-msg");
    const addBtn = document.getElementById("pdp-add-btn");
    const qtyInput = document.getElementById("pdp-qty-input");
    const notifyBox = document.getElementById("pdp-notify-box");
    const notifyVidInput = document.getElementById("notify-variant-id");

    function selectVariant(vid) {
      const v = variants.find((item) => item.id === vid);
      if (!v) return;
      pills.forEach((p) => p.classList.toggle("active", Number(p.getAttribute("data-variant-pill")) === vid));
      if (priceEl) priceEl.textContent = formatToman(v.price_rial);
      if (oldPriceEl) {
        if (v.compare_at_price_rial && v.compare_at_price_rial > v.price_rial) {
          oldPriceEl.textContent = formatToman(v.compare_at_price_rial);
          oldPriceEl.style.display = "inline";
        } else {
          oldPriceEl.style.display = "none";
        }
      }
      if (skuEl) skuEl.textContent = v.sku;
      if (addBtn) addBtn.setAttribute("data-variant-id", String(v.id));
      if (notifyVidInput) notifyVidInput.value = String(v.id);

      if (v.stock_quantity <= 0) {
        if (stockMsgEl) {
          stockMsgEl.textContent = "ناموجود";
          stockMsgEl.className = "badge badge-danger";
        }
        if (addBtn) {
          addBtn.disabled = true;
          addBtn.textContent = "ناموجود";
        }
        if (qtyInput) {
          qtyInput.value = "1";
          qtyInput.max = "1";
          qtyInput.disabled = true;
        }
        if (notifyBox) notifyBox.style.display = "block";
      } else {
        if (v.stock_quantity <= v.low_stock_threshold) {
          if (stockMsgEl) {
            stockMsgEl.textContent = `تنها ${toPersianDigits(v.stock_quantity)} عدد در انبار باقی مانده`;
            stockMsgEl.className = "badge badge-warning";
          }
        } else {
          if (stockMsgEl) {
            stockMsgEl.textContent = "موجود در انبار";
            stockMsgEl.className = "badge badge-success";
          }
        }
        if (addBtn) {
          addBtn.disabled = false;
          addBtn.textContent = "افزودن به سبد خرید";
        }
        if (qtyInput) {
          qtyInput.disabled = false;
          qtyInput.max = String(v.stock_quantity);
          if (Number(qtyInput.value) > v.stock_quantity) qtyInput.value = String(v.stock_quantity);
        }
        if (notifyBox) notifyBox.style.display = "none";
      }
    }

    pills.forEach((p) =>
      p.addEventListener("click", () => selectVariant(Number(p.getAttribute("data-variant-pill"))))
    );
    const initial = variants.find((v) => v.stock_quantity > 0) || variants[0];
    if (initial) selectVariant(initial.id);
  }

  // Product Detail Gallery (images + videos) & Lightbox
  const mainMediaBox = document.getElementById("pdp-main-media");
  const lightbox = document.getElementById("img-lightbox");
  const lightboxImg = document.getElementById("lightbox-img");
  if (mainMediaBox) {
    document.querySelectorAll("[data-media-thumb]").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("[data-media-thumb]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        const kind = btn.getAttribute("data-kind");
        const src = btn.getAttribute("data-src");
        const poster = btn.getAttribute("data-poster") || "";
        const alt = btn.getAttribute("data-alt") || "";
        mainMediaBox.innerHTML = "";
        if (kind === "image") {
          const img = document.createElement("img");
          img.src = src;
          img.alt = alt;
          img.style.cursor = "zoom-in";
          img.addEventListener("click", () => {
            if (lightbox && lightboxImg) {
              lightboxImg.src = src;
              lightbox.classList.add("open");
            }
          });
          mainMediaBox.appendChild(img);
        } else if (kind === "video_upload") {
          const vid = document.createElement("video");
          vid.controls = true;
          vid.preload = "none";
          if (poster) vid.poster = poster;
          const source = document.createElement("source");
          source.src = src;
          vid.appendChild(source);
          mainMediaBox.appendChild(vid);
        } else if (kind === "video_embed") {
          const iframe = document.createElement("iframe");
          iframe.src = src;
          iframe.allowFullscreen = true;
          iframe.loading = "lazy";
          mainMediaBox.appendChild(iframe);
        }
      });
    });
    mainMediaBox.addEventListener("click", (e) => {
      if (e.target.tagName === "IMG" && lightbox && lightboxImg) {
        lightboxImg.src = e.target.src;
        lightbox.classList.add("open");
      }
    });
  }
  if (lightbox) {
    lightbox.addEventListener("click", () => lightbox.classList.remove("open"));
  }

  // Notify me when back in stock
  const notifyForm = document.getElementById("notify-stock-form");
  if (notifyForm) {
    notifyForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(notifyForm);
      const resp = await fetch("/notify-stock", {
        method: "POST",
        headers: { "X-CSRFToken": getCsrfToken() },
        body: fd,
      });
      const data = await resp.json();
      showToast(data.message || "ثبت شد.");
    });
  }

  // Wishlist Toggle
  document.addEventListener("click", async (e) => {
    const wBtn = e.target.closest("[data-wishlist-toggle]");
    if (!wBtn) return;
    const pid = wBtn.getAttribute("data-wishlist-toggle");
    const fd = new FormData();
    fd.append("product_id", pid);
    const resp = await fetch("/wishlist/toggle", {
      method: "POST",
      headers: { "X-CSRFToken": getCsrfToken() },
      body: fd,
    });
    if (resp.status === 401) {
      window.location.href = "/auth/login?next=" + encodeURIComponent(window.location.pathname);
      return;
    }
    const data = await resp.json();
    showToast(data.message);
    wBtn.textContent = data.in_wishlist ? "♥ در علاقه‌مندی‌ها" : "♡ افزودن به علاقه‌مندی";
  });

  // Review Helpful Vote
  document.addEventListener("click", async (e) => {
    const hBtn = e.target.closest("[data-helpful-btn]");
    if (!hBtn) return;
    const rid = hBtn.getAttribute("data-helpful-btn");
    const resp = await fetch(`/review/${rid}/helpful`, {
      method: "POST",
      headers: { "X-CSRFToken": getCsrfToken() },
    });
    const data = await resp.json();
    const countSpan = hBtn.querySelector("[data-helpful-count]");
    if (countSpan) countSpan.textContent = toPersianDigits(data.helpful_count);
  });

  // OTP Request Buttons
  document.addEventListener("click", async (e) => {
    const otpBtn = e.target.closest("[data-send-otp]");
    if (!otpBtn) return;
    const mobileSel = otpBtn.getAttribute("data-mobile-input");
    const purpose = otpBtn.getAttribute("data-purpose") || "login";
    const statusSel = otpBtn.getAttribute("data-status-el");
    const mobileInp = document.querySelector(mobileSel);
    const statusEl = statusSel ? document.querySelector(statusSel) : null;
    if (!mobileInp || !mobileInp.value.trim()) {
      showToast("ابتدا شماره موبایل را وارد کنید.");
      return;
    }

    otpBtn.disabled = true;
    try {
      const resp = await fetch("/auth/otp/request", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(),
        },
        body: JSON.stringify({ mobile: mobileInp.value.trim(), purpose }),
      });
      const data = await resp.json();
      let msg = data.message || "";
      if (data.dev_code) {
        msg += ` (کد آزمایشی: ${data.dev_code})`;
      }
      if (statusEl) {
        statusEl.textContent = msg;
        statusEl.style.display = "block";
      } else {
        showToast(msg);
      }

      let sec = data.ok ? 60 : Number(data.cooldown || 0);
      if (sec > 0) {
        const origText = otpBtn.getAttribute("data-orig-text") || otpBtn.textContent;
        otpBtn.setAttribute("data-orig-text", origText);
        const timer = setInterval(() => {
          sec -= 1;
          if (sec <= 0) {
            clearInterval(timer);
            otpBtn.disabled = false;
            otpBtn.textContent = origText;
          } else {
            otpBtn.textContent = `ارسال مجدد (${toPersianDigits(sec)})`;
          }
        }, 1000);
      } else {
        otpBtn.disabled = false;
      }
    } catch {
      otpBtn.disabled = false;
      showToast("خطا در ارسال کد تایید.");
    }
  });

  // Province -> Cities dynamic select
  document.addEventListener("change", async (e) => {
    const provSel = e.target.closest("[data-province-select]");
    if (!provSel) return;
    const citySelId = provSel.getAttribute("data-city-target");
    const citySel = document.getElementById(citySelId);
    if (!citySel) return;
    const pid = provSel.value;
    citySel.innerHTML = '<option value="">در حال بارگذاری...</option>';
    if (!pid) {
      citySel.innerHTML = '<option value="">ابتدا استان را انتخاب کنید</option>';
      return;
    }
    const resp = await fetch(`/api/provinces/${pid}/cities`);
    const cities = await resp.json();
    citySel.innerHTML = '<option value="">انتخاب شهر</option>';
    cities.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = c.name;
      citySel.appendChild(opt);
    });
  });

  // Recently Viewed Products (localStorage)
  const pdpMeta = document.getElementById("pdp-recent-meta");
  if (pdpMeta) {
    try {
      const item = JSON.parse(pdpMeta.textContent);
      const list = JSON.parse(localStorage.getItem("armin_recent_products") || "[]")
        .filter((x) => x.slug !== item.slug);
      list.unshift(item);
      localStorage.setItem("armin_recent_products", JSON.stringify(list.slice(0, 8)));
    } catch {}
  }
  const recentHost = document.getElementById("recently-viewed-list");
  if (recentHost) {
    try {
      const currentSlug = recentHost.getAttribute("data-exclude-slug") || "";
      const list = JSON.parse(localStorage.getItem("armin_recent_products") || "[]")
        .filter((x) => x.slug !== currentSlug)
        .slice(0, 4);
      if (list.length > 0) {
        document.getElementById("recently-viewed-section").style.display = "block";
        list.forEach((it) => {
          const card = document.createElement("a");
          card.href = `/product/${encodeURIComponent(it.slug)}`;
          card.className = "product-card";
          card.innerHTML = `
            <div class="product-thumb">${it.thumb ? `<img src="/media/${it.thumb}" alt="${it.title}" loading="lazy">` : ""}</div>
            <div class="product-body">
              <h3 class="product-title">${it.title}</h3>
              <div class="product-price-row"><span class="price-current">${it.price_str}</span></div>
            </div>`;
          recentHost.appendChild(card);
        });
      }
    } catch {}
  }

  // Sticky header elevation on scroll
  const headerEl = document.querySelector(".site-header");
  if (headerEl) {
    const onScroll = () => headerEl.classList.toggle("scrolled", window.scrollY > 18);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  // Tasteful staggered scroll-reveal animations
  const revealTargets = document.querySelectorAll(
    ".section-header, .cat-card, .product-card, .trust-card"
  );
  if (revealTargets.length > 0 && "IntersectionObserver" in window) {
    revealTargets.forEach((el, idx) => {
      el.setAttribute("data-reveal", "");
      el.style.transitionDelay = `${(idx % 4) * 55}ms`;
    });
    const observer = new IntersectionObserver(
      (entries, obs) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            obs.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.08, rootMargin: "0px 0px -24px 0px" }
    );
    revealTargets.forEach((el) => observer.observe(el));
  }
})();
