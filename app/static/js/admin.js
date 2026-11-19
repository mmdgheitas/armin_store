(function () {
  "use strict";

  // Select All checkboxes in admin tables
  document.addEventListener("change", (e) => {
    const master = e.target.closest("[data-select-all]");
    if (!master) return;
    const selector = master.getAttribute("data-select-all");
    document.querySelectorAll(selector).forEach((cb) => {
      cb.checked = master.checked;
    });
  });

  // Destructive confirmation dialogs
  document.addEventListener("submit", (e) => {
    const form = e.target.closest("form[data-confirm]");
    if (!form) return;
    const msg = form.getAttribute("data-confirm");
    if (!window.confirm(msg)) {
      e.preventDefault();
    }
  });

  // Unsaved changes warning on marked forms
  const dirtyForm = document.querySelector("form[data-warn-unsaved]");
  if (dirtyForm) {
    let isDirty = false;
    dirtyForm.addEventListener("input", () => { isDirty = true; });
    dirtyForm.addEventListener("change", () => { isDirty = true; });
    dirtyForm.addEventListener("submit", () => { isDirty = false; });
    window.addEventListener("beforeunload", (e) => {
      if (isDirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    });
  }

  // Dynamic Variant Rows on Product Form
  const variantsBody = document.getElementById("variants-tbody");
  const addVariantBtn = document.getElementById("add-variant-btn");
  if (variantsBody && addVariantBtn) {
    addVariantBtn.addEventListener("click", () => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><input type="hidden" name="variant_id[]" value=""><input type="text" name="variant_label[]" class="form-control" placeholder="مثلاً ۵ کیلوگرم" required></td>
        <td><input type="text" name="variant_sku[]" class="form-control" dir="ltr" placeholder="SKU-..." required></td>
        <td><input type="number" name="variant_price_toman[]" class="form-control" min="0" required></td>
        <td><input type="number" name="variant_compare_toman[]" class="form-control" min="0"></td>
        <td><input type="number" name="variant_weight[]" class="form-control" min="0" value="1000"></td>
        <td><input type="number" name="variant_stock[]" class="form-control" min="0" value="10"></td>
        <td><input type="number" name="variant_threshold[]" class="form-control" min="0" value="5"></td>
        <td><select name="variant_active[]" class="form-control"><option value="1">فعال</option><option value="0">غیرفعال</option></select></td>
        <td><button type="button" class="btn btn-sm btn-danger" onclick="this.closest('tr').remove()">حذف</button></td>
      `;
      variantsBody.appendChild(tr);
    });
  }

  // Dynamic Specs Rows
  const specsList = document.getElementById("specs-list");
  const addSpecBtn = document.getElementById("add-spec-btn");
  if (specsList && addSpecBtn) {
    addSpecBtn.addEventListener("click", () => {
      const row = document.createElement("div");
      row.className = "form-row";
      row.style.marginBottom = "8px";
      row.innerHTML = `
        <input type="text" name="spec_key[]" class="form-control" placeholder="عنوان ویژگی (مثلاً محل کشت)">
        <input type="text" name="spec_value[]" class="form-control" placeholder="مقدار (مثلاً فریدونکنار مازندران)">
        <button type="button" class="btn btn-sm btn-danger" onclick="this.parentElement.remove()">حذف</button>
      `;
      specsList.appendChild(row);
    });
  }

  // SEO Live Counters & SERP Preview
  const titleInp = document.getElementById("prod-title-input");
  const slugInp = document.getElementById("prod-slug-input");
  const metaTitleInp = document.getElementById("meta-title-input");
  const metaDescInp = document.getElementById("meta-desc-input");
  const metaTitleCount = document.getElementById("meta-title-count");
  const metaDescCount = document.getElementById("meta-desc-count");
  const serpTitle = document.getElementById("serp-title");
  const serpUrl = document.getElementById("serp-url");
  const serpDesc = document.getElementById("serp-desc");

  function updateSerpPreview() {
    const t = (metaTitleInp && metaTitleInp.value.trim()) || (titleInp && titleInp.value.trim()) || "عنوان محصول";
    const d = (metaDescInp && metaDescInp.value.trim()) || "توضیحات متا در نتایج جستجوی گوگل...";
    const s = (slugInp && slugInp.value.trim()) || "product-slug";
    if (metaTitleCount) metaTitleCount.textContent = String((metaTitleInp ? metaTitleInp.value : "").length);
    if (metaDescCount) metaDescCount.textContent = String((metaDescInp ? metaDescInp.value : "").length);
    if (serpTitle) serpTitle.textContent = t;
    if (serpDesc) serpDesc.textContent = d;
    if (serpUrl) serpUrl.textContent = window.location.origin + "/product/" + s;
  }

  [titleInp, slugInp, metaTitleInp, metaDescInp].forEach((el) => {
    if (el) el.addEventListener("input", updateSerpPreview);
  });
  if (serpTitle) updateSerpPreview();

  // Drag & Drop Image Preview
  const dropzone = document.getElementById("image-dropzone");
  const fileInput = document.getElementById("new-images-input");
  const previewBox = document.getElementById("new-images-preview");
  if (dropzone && fileInput) {
    dropzone.addEventListener("click", () => fileInput.click());
    dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("dragover"); });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
      fileInput.files = e.dataTransfer.files;
      renderImagePreviews(fileInput.files);
    });
    fileInput.addEventListener("change", () => renderImagePreviews(fileInput.files));
  }

  function renderImagePreviews(files) {
    if (!previewBox) return;
    previewBox.innerHTML = "";
    Array.from(files || []).forEach((f) => {
      const reader = new FileReader();
      reader.onload = (ev) => {
        const img = document.createElement("img");
        img.src = ev.target.result;
        img.style.width = "84px";
        img.style.height = "68px";
        img.style.objectFit = "cover";
        img.style.borderRadius = "8px";
        img.style.border = "1px solid #d1d5db";
        previewBox.appendChild(img);
      };
      reader.readAsDataURL(f);
    });
  }

  // Province -> City dynamic select in admin manual order
  document.addEventListener("change", async (e) => {
    const provSel = e.target.closest("[data-province-select]");
    if (!provSel) return;
    const citySel = document.getElementById(provSel.getAttribute("data-city-target"));
    if (!citySel || !provSel.value) return;
    const resp = await fetch(`/api/provinces/${provSel.value}/cities`);
    const cities = await resp.json();
    citySel.innerHTML = '<option value="">انتخاب شهر</option>';
    cities.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = c.name;
      citySel.appendChild(opt);
    });
  });

  // Vanilla SVG Revenue Chart on Dashboard
  const chartHost = document.getElementById("revenue-chart");
  const chartDataEl = document.getElementById("revenue-chart-data");
  if (chartHost && chartDataEl) {
    const payload = JSON.parse(chartDataEl.textContent || "{}");
    const labels = payload.labels || [];
    const values = payload.values || [];
    const maxVal = Math.max(1, ...values);
    const w = 720;
    const h = 220;
    const pad = 28;
    const barW = Math.max(12, Math.floor((w - pad * 2) / Math.max(1, values.length)) - 8);

    let svg = `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto;display:block">`;
    values.forEach((val, i) => {
      const bh = Math.round(((h - pad * 2) * val) / maxVal);
      const x = pad + i * (barW + 8);
      const y = h - pad - bh;
      svg += `<rect x="${x}" y="${y}" width="${barW}" height="${Math.max(2, bh)}" rx="4" fill="#2e5d3b"><title>${labels[i]}: ${val.toLocaleString()} تومان</title></rect>`;
      if (i % 2 === 0 || values.length <= 7) {
        svg += `<text x="${x + barW / 2}" y="${h - 8}" font-size="10" text-anchor="middle" fill="#6b7280">${(labels[i] || "").slice(5)}</text>`;
      }
    });
    svg += "</svg>";
    chartHost.innerHTML = svg;
  }
})();
