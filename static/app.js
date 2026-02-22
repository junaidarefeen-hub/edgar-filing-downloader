// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const DEFAULT_TYPES = [
    "10-K", "10-Q", "8-K", "DEF 14A", "20-F", "S-1",
    "10-K/A", "10-Q/A", "8-K/A", "S-3", "S-4",
    "SC 13D", "SC 13G", "6-K", "DEFA14A",
];

let state = {
    ticker: null,
    company: null,
    cik: null,
    allFilings: [],
    filingTypes: [],
    selectedTypes: new Set(),
    startYear: null,
    endYear: null,
    downloading: false,
};

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const show = (id) => $(id).classList.remove("hidden");
const hide = (id) => $(id).classList.add("hidden");

// ---------------------------------------------------------------------------
// Logo helpers
// ---------------------------------------------------------------------------

const LOGO_COLORS = [
    "#2563eb", "#7c3aed", "#db2777", "#ea580c",
    "#16a34a", "#0891b2", "#4f46e5", "#c026d3",
];

function getLogoColor(ticker) {
    let hash = 0;
    for (let i = 0; i < ticker.length; i++) {
        hash = ticker.charCodeAt(i) + ((hash << 5) - hash);
    }
    return LOGO_COLORS[Math.abs(hash) % LOGO_COLORS.length];
}

function showLogoFallback(ticker, imgEl, fallbackEl) {
    imgEl.classList.add("hidden");
    fallbackEl.textContent = ticker.charAt(0);
    fallbackEl.style.backgroundColor = getLogoColor(ticker);
    fallbackEl.classList.remove("hidden");
}

// ---------------------------------------------------------------------------
// Filing type badge helper
// ---------------------------------------------------------------------------

function filingTypeBadgeClass(formType) {
    const clean = formType.replace(/[\s\/]/g, "-");
    const knownTypes = [
        "10-K", "10-Q", "8-K", "DEF-14A", "DEFA14A",
        "20-F", "S-1", "S-3", "S-4", "SC-13D", "SC-13G", "6-K",
    ];
    // Check common prefixes for amended forms like 10-K-A
    for (const t of knownTypes) {
        if (clean.startsWith(t)) return "type-" + t;
    }
    return "type-default";
}

// ---------------------------------------------------------------------------
// Ticker lookup
// ---------------------------------------------------------------------------

async function lookupTicker() {
    const input = $("ticker-input");
    const ticker = input.value.trim().toUpperCase();
    if (!ticker) return;

    hide("error-msg");
    hide("company-section");
    show("spinner");
    $("lookup-btn").disabled = true;

    try {
        const resp = await fetch("/api/lookup", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ticker }),
        });
        const data = await resp.json();

        if (!resp.ok) {
            $("error-msg").textContent = data.error || "Lookup failed.";
            show("error-msg");
            return;
        }

        state.ticker = ticker;
        state.company = data.company;
        state.cik = data.cik;
        state.allFilings = data.filings;
        state.filingTypes = data.filingTypes;
        state.startYear = data.dateRange.min;
        state.endYear = data.dateRange.max;

        renderCompanyInfo();
        renderFilingTypes();
        populateDateDropdowns();
        filterAndRenderTable();
        show("company-section");
        hide("progress-section");
        hide("progress-done");
    } catch (err) {
        $("error-msg").textContent = "Network error. Is the server running?";
        show("error-msg");
    } finally {
        hide("spinner");
        $("lookup-btn").disabled = false;
    }
}

// Enter key triggers lookup
document.addEventListener("DOMContentLoaded", () => {
    $("ticker-input").addEventListener("keydown", (e) => {
        if (e.key === "Enter") lookupTicker();
    });
});

// ---------------------------------------------------------------------------
// Render company info (with logo)
// ---------------------------------------------------------------------------

function renderCompanyInfo() {
    $("company-name").textContent = state.company;
    $("company-cik").textContent = `CIK: ${state.cik}`;

    const logoImg = $("company-logo");
    const logoFallback = $("company-logo-fallback");

    if (typeof LOGO_DEV_TOKEN !== "undefined" && LOGO_DEV_TOKEN) {
        logoImg.src = `https://img.logo.dev/ticker/${state.ticker}?token=${LOGO_DEV_TOKEN}&size=64&format=png`;
        logoImg.alt = state.ticker;
        logoImg.classList.remove("hidden");
        logoFallback.classList.add("hidden");
        logoImg.onerror = () => showLogoFallback(state.ticker, logoImg, logoFallback);
    } else {
        showLogoFallback(state.ticker, logoImg, logoFallback);
    }
}

// ---------------------------------------------------------------------------
// Filing type checkboxes
// ---------------------------------------------------------------------------

function renderFilingTypes() {
    const container = $("filing-types");
    container.innerHTML = "";

    // Pre-check types that are in the default list
    state.selectedTypes = new Set(
        state.filingTypes.filter((t) => DEFAULT_TYPES.includes(t))
    );

    state.filingTypes.forEach((type) => {
        const label = document.createElement("label");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = type;
        cb.checked = state.selectedTypes.has(type);
        cb.addEventListener("change", () => {
            if (cb.checked) {
                state.selectedTypes.add(type);
            } else {
                state.selectedTypes.delete(type);
            }
            filterAndRenderTable();
        });
        label.appendChild(cb);
        label.appendChild(document.createTextNode(" " + type));
        container.appendChild(label);
    });
}

function selectAllTypes() {
    state.selectedTypes = new Set(state.filingTypes);
    document.querySelectorAll("#filing-types input[type=checkbox]").forEach(
        (cb) => (cb.checked = true)
    );
    filterAndRenderTable();
}

function deselectAllTypes() {
    state.selectedTypes.clear();
    document.querySelectorAll("#filing-types input[type=checkbox]").forEach(
        (cb) => (cb.checked = false)
    );
    filterAndRenderTable();
}

// ---------------------------------------------------------------------------
// Date range dropdowns
// ---------------------------------------------------------------------------

function populateDateDropdowns() {
    const startSel = $("start-year");
    const endSel = $("end-year");
    startSel.innerHTML = "";
    endSel.innerHTML = "";

    for (let y = state.startYear; y <= state.endYear; y++) {
        const opt1 = document.createElement("option");
        opt1.value = y;
        opt1.textContent = y;
        startSel.appendChild(opt1);

        const opt2 = document.createElement("option");
        opt2.value = y;
        opt2.textContent = y;
        endSel.appendChild(opt2);
    }

    startSel.value = state.startYear;
    endSel.value = state.endYear;
}

// ---------------------------------------------------------------------------
// Filter and render table
// ---------------------------------------------------------------------------

function getFilteredFilings() {
    const startYear = parseInt($("start-year").value);
    const endYear = parseInt($("end-year").value);

    return state.allFilings.filter((f) => {
        if (!state.selectedTypes.has(f.form)) return false;
        const year = parseInt(f.filingDate.substring(0, 4));
        return year >= startYear && year <= endYear;
    });
}

function filterAndRenderTable() {
    const filtered = getFilteredFilings();
    const tbody = $("filings-tbody");
    tbody.innerHTML = "";

    filtered.forEach((f) => {
        const tr = document.createElement("tr");
        const badgeClass = filingTypeBadgeClass(f.form);
        tr.innerHTML = `
            <td><span class="filing-type-badge ${badgeClass}">${escapeHtml(f.form)}</span></td>
            <td>${escapeHtml(f.filingDate)}</td>
            <td>${escapeHtml(f.primaryDocDescription || f.primaryDocument)}</td>
        `;
        tbody.appendChild(tr);
    });

    $("filing-count-num").textContent = filtered.length;
    $("download-count").textContent = filtered.length;
    $("download-btn").disabled = filtered.length === 0 || state.downloading;
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Download
// ---------------------------------------------------------------------------

async function startDownload() {
    const filtered = getFilteredFilings();
    if (filtered.length === 0) return;

    state.downloading = true;
    $("download-btn").disabled = true;
    hide("progress-done");
    hide("progress-errors");
    show("progress-section");
    $("progress-bar").style.width = "0%";
    $("progress-text").textContent = `0 / ${filtered.length}`;
    $("progress-current").textContent = "";

    try {
        const resp = await fetch("/api/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                ticker: state.ticker,
                cik: state.cik,
                filings: filtered,
            }),
        });
        const data = await resp.json();

        if (!resp.ok) {
            $("error-msg").textContent = data.error || "Download request failed.";
            show("error-msg");
            state.downloading = false;
            $("download-btn").disabled = false;
            return;
        }

        trackProgress(data.jobId);
    } catch (err) {
        $("error-msg").textContent = "Network error starting download.";
        show("error-msg");
        state.downloading = false;
        $("download-btn").disabled = false;
    }
}

// ---------------------------------------------------------------------------
// SSE progress tracking
// ---------------------------------------------------------------------------

function trackProgress(jobId) {
    const source = new EventSource(`/api/progress/${jobId}`);

    source.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.error) {
            source.close();
            $("error-msg").textContent = data.error;
            show("error-msg");
            state.downloading = false;
            $("download-btn").disabled = false;
            return;
        }

        const pct = data.total > 0 ? (data.completed / data.total) * 100 : 0;
        $("progress-bar").style.width = pct + "%";
        $("progress-text").textContent = `${data.completed} / ${data.total}`;
        $("progress-current").textContent = data.current || "";

        // Show errors if any
        if (data.errors && data.errors.length > 0) {
            $("error-count").textContent = data.errors.length;
            const list = $("error-list");
            list.innerHTML = "";
            data.errors.forEach((e) => {
                const li = document.createElement("li");
                li.textContent = `${e.filing}: ${e.error}`;
                list.appendChild(li);
            });
            show("progress-errors");
        }

        if (data.status === "done") {
            source.close();
            show("progress-done");
            state.downloading = false;
            $("download-btn").disabled = false;
        }
    };

    source.onerror = () => {
        source.close();
        $("progress-current").textContent = "Connection lost. Check server.";
        state.downloading = false;
        $("download-btn").disabled = false;
    };
}
