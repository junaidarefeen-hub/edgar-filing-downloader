// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let filingsData = {};       // { ticker: { form_type: [{date, path, filename, indexed}] } }
let selectedTicker = "";
let selectedPaths = new Set();
let querying = false;

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

// ---------------------------------------------------------------------------
// Gemini setup
// ---------------------------------------------------------------------------

async function saveGeminiKey() {
    const input = $("gemini-key-input");
    const key = input.value.trim();
    if (!key) return;

    $("gemini-save-btn").disabled = true;
    hide("gemini-setup-error");

    try {
        const resp = await fetch("/api/gemini-setup", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ apiKey: key }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            $("gemini-setup-error").textContent = data.error;
            show("gemini-setup-error");
            return;
        }
        hide("gemini-setup");
        show("main-content");
        fetchFilings();
    } catch (err) {
        $("gemini-setup-error").textContent = "Failed to save. Is the server running?";
        show("gemini-setup-error");
    } finally {
        $("gemini-save-btn").disabled = false;
    }
}

// ---------------------------------------------------------------------------
// Fetch filings
// ---------------------------------------------------------------------------

async function fetchFilings() {
    try {
        const resp = await fetch("/api/filings");
        filingsData = await resp.json();
        renderFilingBrowser();
        populateTickerSelect();
    } catch (err) {
        $("filing-browser").innerHTML = '<div class="empty-state">Failed to load filings.</div>';
    }
}

function populateTickerSelect() {
    const select = $("ticker-select");
    select.innerHTML = '<option value="">Select a ticker</option>';
    for (const ticker of Object.keys(filingsData).sort()) {
        const opt = document.createElement("option");
        opt.value = ticker;
        opt.textContent = ticker;
        select.appendChild(opt);
    }
}

function renderFilingBrowser() {
    const browser = $("filing-browser");
    const tickers = Object.keys(filingsData).sort();

    if (tickers.length === 0) {
        browser.innerHTML = '<div class="empty-state">No downloaded filings found. Download some filings first from the <a href="/">main page</a>.</div>';
        return;
    }

    browser.innerHTML = "";
    for (const ticker of tickers) {
        const group = document.createElement("div");
        group.className = "ticker-group";

        const h3 = document.createElement("h3");

        // Add logo or fallback
        if (typeof LOGO_DEV_TOKEN !== "undefined" && LOGO_DEV_TOKEN) {
            const img = document.createElement("img");
            img.className = "ticker-logo";
            img.src = `https://img.logo.dev/ticker/${ticker}?token=${LOGO_DEV_TOKEN}&size=32&format=png`;
            img.alt = ticker;
            img.onerror = function() {
                const fallback = document.createElement("span");
                fallback.className = "ticker-logo-fallback";
                fallback.textContent = ticker.charAt(0);
                fallback.style.backgroundColor = getLogoColor(ticker);
                this.replaceWith(fallback);
            };
            h3.appendChild(img);
        } else {
            const fallback = document.createElement("span");
            fallback.className = "ticker-logo-fallback";
            fallback.textContent = ticker.charAt(0);
            fallback.style.backgroundColor = getLogoColor(ticker);
            h3.appendChild(fallback);
        }

        h3.appendChild(document.createTextNode(ticker));
        group.appendChild(h3);

        const formTypes = filingsData[ticker];
        for (const formType of Object.keys(formTypes).sort()) {
            const formGroup = document.createElement("div");
            formGroup.className = "form-group";

            const h4 = document.createElement("h4");
            h4.textContent = formType.replace("_", "/");
            formGroup.appendChild(h4);

            for (const filing of formTypes[formType]) {
                const item = document.createElement("div");
                item.className = "filing-item";

                const cb = document.createElement("input");
                cb.type = "checkbox";
                cb.dataset.path = filing.path;
                cb.dataset.ticker = ticker;
                cb.addEventListener("change", updateSelection);

                const label = document.createElement("label");
                label.textContent = `${filing.date} — ${filing.filename}`;
                label.prepend(cb);

                const badge = document.createElement("span");
                badge.className = "badge " + (filing.indexed ? "badge-indexed" : "badge-not-indexed");
                badge.textContent = filing.indexed ? " indexed" : " not indexed";

                item.appendChild(label);
                item.appendChild(badge);
                formGroup.appendChild(item);
            }

            group.appendChild(formGroup);
        }

        browser.appendChild(group);
    }
}

function updateSelection() {
    selectedPaths.clear();
    const checkboxes = document.querySelectorAll("#filing-browser input[type=checkbox]:checked");
    checkboxes.forEach((cb) => selectedPaths.add(cb.dataset.path));
    $("index-btn").disabled = selectedPaths.size === 0;
}

function onTickerChange() {
    selectedTicker = $("ticker-select").value;
    $("send-btn").disabled = !selectedTicker;
    populateFilingTypeFilters();
}

// ---------------------------------------------------------------------------
// Query filters
// ---------------------------------------------------------------------------

function populateFilingTypeFilters() {
    const container = $("filing-type-checkboxes");
    const filtersSection = $("query-filters");
    container.innerHTML = "";

    if (!selectedTicker || !filingsData[selectedTicker]) {
        filtersSection.classList.add("hidden");
        return;
    }

    filtersSection.classList.remove("hidden");

    const formTypes = Object.keys(filingsData[selectedTicker]).sort();
    for (const ft of formTypes) {
        const label = document.createElement("label");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = true;
        cb.value = ft;
        cb.className = "filter-type-cb";
        label.appendChild(cb);
        label.appendChild(document.createTextNode(" " + ft.replace("_", "/")));
        container.appendChild(label);
    }
}

function clearFilters() {
    $("filter-date-from").value = "";
    $("filter-date-to").value = "";
    document.querySelectorAll(".filter-type-cb").forEach(cb => { cb.checked = true; });
}

function getFilterParams() {
    const params = {};
    const dateFrom = $("filter-date-from").value;
    const dateTo = $("filter-date-to").value;
    if (dateFrom) params.dateFrom = dateFrom;
    if (dateTo) params.dateTo = dateTo;

    // Only send filingTypes when not all are checked (all-checked = no filter)
    const allCbs = document.querySelectorAll(".filter-type-cb");
    const checkedCbs = document.querySelectorAll(".filter-type-cb:checked");
    if (allCbs.length > 0 && checkedCbs.length < allCbs.length) {
        params.filingTypes = [...checkedCbs].map(cb => cb.value);
    }

    return params;
}

function selectAllFilings() {
    const ticker = $("ticker-select").value;
    document.querySelectorAll("#filing-browser input[type=checkbox]").forEach((cb) => {
        if (!ticker || cb.dataset.ticker === ticker) {
            cb.checked = true;
        }
    });
    updateSelection();
}

function deselectAllFilings() {
    document.querySelectorAll("#filing-browser input[type=checkbox]").forEach((cb) => {
        cb.checked = false;
    });
    updateSelection();
}

// ---------------------------------------------------------------------------
// Index filings
// ---------------------------------------------------------------------------

async function indexSelected() {
    if (selectedPaths.size === 0) return;

    // Determine ticker from selected paths
    const firstPath = [...selectedPaths][0];
    let ticker = $("ticker-select").value;
    if (!ticker) {
        // Try to infer from checkboxes
        const cb = document.querySelector("#filing-browser input[type=checkbox]:checked");
        if (cb) ticker = cb.dataset.ticker;
    }
    if (!ticker) return;

    $("index-btn").disabled = true;
    show("index-progress");
    $("index-progress-bar").style.width = "0%";
    $("index-progress-msg").textContent = "Starting indexing...";

    try {
        const resp = await fetch("/api/index", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                ticker: ticker,
                filings: [...selectedPaths],
            }),
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                const data = JSON.parse(line.slice(6));

                if (data.status === "progress") {
                    const pct = data.total > 0 ? (data.current / data.total) * 100 : 0;
                    $("index-progress-bar").style.width = pct + "%";
                    $("index-progress-msg").textContent = data.message || "Indexing...";
                } else if (data.status === "done") {
                    $("index-progress-bar").style.width = "100%";
                    const s = data.stats;
                    $("index-progress-msg").textContent =
                        `Done! Indexed: ${s.indexed}, Skipped: ${s.skipped}, Chunks: ${s.total_chunks}`;
                    // Refresh filing list to update badges
                    fetchFilings();
                } else if (data.status === "error") {
                    $("index-progress-msg").textContent = "Error: " + data.error;
                }
            }
        }
    } catch (err) {
        $("index-progress-msg").textContent = "Error: " + err.message;
    } finally {
        $("index-btn").disabled = false;
    }
}

// ---------------------------------------------------------------------------
// Chat / Query
// ---------------------------------------------------------------------------

function addChatMessage(role, text) {
    const messages = $("chat-messages");
    const empty = messages.querySelector(".empty-state");
    if (empty) empty.remove();

    const div = document.createElement("div");
    div.className = "chat-msg " + (role === "user" ? "chat-msg-user" : "chat-msg-assistant");

    if (role === "assistant") {
        const label = document.createElement("span");
        label.className = "chat-msg-label";
        label.textContent = "Gemini";
        div.appendChild(label);
    }

    const content = document.createElement("span");
    content.className = "chat-msg-content";
    content.textContent = text;
    div.appendChild(content);

    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return content;
}

function addChatMessageDiv(role) {
    const messages = $("chat-messages");
    const empty = messages.querySelector(".empty-state");
    if (empty) empty.remove();

    const div = document.createElement("div");
    div.className = "chat-msg " + (role === "user" ? "chat-msg-user" : "chat-msg-assistant");

    if (role === "assistant") {
        const label = document.createElement("span");
        label.className = "chat-msg-label";
        label.textContent = "Gemini";
        div.appendChild(label);
    }

    const content = document.createElement("span");
    content.className = "chat-msg-content";
    div.appendChild(content);

    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
}

async function sendQuery() {
    const input = $("chat-input");
    const question = input.value.trim();
    const ticker = $("ticker-select").value;

    if (!question || !ticker || querying) return;

    querying = true;
    $("send-btn").disabled = true;
    input.value = "";

    addChatMessage("user", question);

    // Create a wrapper div for sources + assistant text
    const msgDiv = addChatMessageDiv("assistant");
    const contentSpan = msgDiv.querySelector(".chat-msg-content");

    try {
        const model = $("model-select").value;
        const filters = getFilterParams();
        const resp = await fetch("/api/query", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ticker, question, model, ...filters }),
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let fullText = "";
        let thinkingDetails = null;
        let thinkingBody = null;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                const data = JSON.parse(line.slice(6));

                if (data.status === "sources") {
                    const sourcesLine = data.sources
                        .map(s => `${s.filing_type.replace("_", "/")} ${s.filing_date}`)
                        .join(", ");
                    const tag = document.createElement("div");
                    tag.className = "chat-sources";
                    tag.textContent = "Sources: " + sourcesLine;
                    // Insert before the content span
                    msgDiv.insertBefore(tag, contentSpan);
                } else if (data.status === "thinking") {
                    if (!thinkingDetails) {
                        thinkingDetails = document.createElement("details");
                        thinkingDetails.className = "chat-thinking";
                        const summary = document.createElement("summary");
                        summary.textContent = "Thinking...";
                        thinkingDetails.appendChild(summary);
                        thinkingBody = document.createElement("div");
                        thinkingBody.className = "chat-thinking-body";
                        thinkingDetails.appendChild(thinkingBody);
                        msgDiv.insertBefore(thinkingDetails, contentSpan);
                    }
                    thinkingBody.textContent += data.text;
                    $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
                } else if (data.status === "streaming") {
                    fullText += data.text;
                    contentSpan.textContent = fullText;
                    $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
                } else if (data.status === "error") {
                    contentSpan.textContent = "Error: " + data.error;
                }
            }
        }

        if (!fullText) {
            contentSpan.textContent = contentSpan.textContent || "No response received.";
        }
    } catch (err) {
        contentSpan.textContent = "Error: " + err.message;
    } finally {
        querying = false;
        $("send-btn").disabled = !ticker;
    }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
    // Only fetch if Gemini is configured (main-content is visible)
    if (!$("main-content").classList.contains("hidden")) {
        fetchFilings();
    }
});
