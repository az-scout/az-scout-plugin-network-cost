/* eslint-disable @microsoft/sdl/no-inner-html -- Dynamic values use escapeHtml(). */
// VNet Peering Cost Analyser — 3-step workflow with 3 analysis modes
// Globals from app.js: apiFetch, apiPost, escapeHtml
(function () {
    const PLUGIN = "network-cost";
    const container = document.getElementById("plugin-tab-" + PLUGIN);
    if (!container) return;

    fetch(`/plugins/${PLUGIN}/static/html/network-cost-tab.html`)
        .then(r => r.text())
        .then(html => { container.innerHTML = html; init(); })
        .catch(e => {
            container.innerHTML = `<div class="alert alert-danger">Failed to load plugin UI: ${e.message}</div>`;
        });

    function init() {
        // ---------------------------------------------------------------
        // State
        // ---------------------------------------------------------------
        let currentStep = 1;
        let selectedMode = null; // "estimate" | "billing" | "traffic"
        let analysisData = null; // stores last analysis result + insights

        const $ = id => document.getElementById(id);

        // ---------------------------------------------------------------
        // Stepper
        // ---------------------------------------------------------------
        const steps = container.querySelectorAll(".nc-step");
        const panels = [null, $("nc-step-1"), $("nc-step-2"), $("nc-step-3")];

        function goToStep(n) {
            if (n < 1 || n > 3) return;
            currentStep = n;
            steps.forEach(s => {
                const sn = parseInt(s.dataset.step);
                s.classList.toggle("nc-step-active", sn === n);
                s.classList.toggle("nc-step-done", sn < n);
            });
            panels.forEach((p, i) => { if (p) p.style.display = i === n ? "" : "none"; });
            hideError();
        }

        steps.forEach(s => s.addEventListener("click", () => {
            const sn = parseInt(s.dataset.step);
            if (sn < currentStep) goToStep(sn);
        }));

        // ---------------------------------------------------------------
        // Mode selection
        // ---------------------------------------------------------------
        const modeCards = container.querySelectorAll(".nc-mode-card");
        const prereqs = {
            estimate: $("nc-prereq-estimate"),
            billing: $("nc-prereq-billing"),
            traffic: $("nc-prereq-traffic"),
        };

        modeCards.forEach(card => card.addEventListener("click", () => {
            const mode = card.dataset.mode;
            selectedMode = mode;
            modeCards.forEach(c => c.classList.toggle("nc-mode-selected", c === card));
            Object.entries(prereqs).forEach(([k, el]) => {
                el.style.display = k === mode ? "" : "none";
            });
        }));

        // Billing tab switching (EA / MCA)
        const billingTabBtns = container.querySelectorAll("[data-billing-tab]");
        billingTabBtns.forEach(btn => btn.addEventListener("click", () => {
            const tab = btn.dataset.billingTab;
            billingTabBtns.forEach(b => b.classList.toggle("nc-tab-active", b === btn));
            $("nc-instr-ea").style.display = tab === "ea" ? "" : "none";
            $("nc-instr-mca").style.display = tab === "mca" ? "" : "none";
        }));

        // ---------------------------------------------------------------
        // Continue buttons (Step 1 → Step 2)
        // ---------------------------------------------------------------
        $("nc-btn-continue-estimate").addEventListener("click", () => {
            showAnalysis("estimate");
            goToStep(2);
        });
        $("nc-btn-continue-billing").addEventListener("click", () => {
            showAnalysis("billing");
            goToStep(2);
        });
        $("nc-btn-continue-traffic").addEventListener("click", () => {
            showAnalysis("traffic");
            goToStep(2);
        });

        function showAnalysis(mode) {
            $("nc-analysis-estimate").style.display = mode === "estimate" ? "" : "none";
            $("nc-analysis-billing").style.display = mode === "billing" ? "" : "none";
            $("nc-analysis-traffic").style.display = mode === "traffic" ? "" : "none";
            $("nc-step2-nav").style.display = "";
            $("nc-btn-to-step3").disabled = true;
            analysisData = null;
        }

        // Back / navigation
        $("nc-btn-back-to-1").addEventListener("click", () => goToStep(1));
        $("nc-btn-back-to-2").addEventListener("click", () => goToStep(2));
        $("nc-btn-restart").addEventListener("click", () => {
            selectedMode = null;
            analysisData = null;
            modeCards.forEach(c => c.classList.remove("nc-mode-selected"));
            Object.values(prereqs).forEach(el => { el.style.display = "none"; });
            $("nc-estimate-results").style.display = "none";
            $("nc-billing-results").style.display = "none";
            $("nc-traffic-results").style.display = "none";
            goToStep(1);
        });
        $("nc-btn-to-step3").addEventListener("click", () => {
            if (analysisData) {
                renderInsights(analysisData.insights);
                goToStep(3);
            }
        });

        // ---------------------------------------------------------------
        // Mode 1 — Estimate
        // ---------------------------------------------------------------
        const sourceEl = $("nc-source-region");
        const targetEl = $("nc-target-region");
        const trafficAB = $("nc-traffic-ab");
        const trafficBA = $("nc-traffic-ba");
        const scenarioEl = $("nc-scenario");
        const estimateBtn = $("nc-estimate-btn");

        loadRegions();
        estimateBtn.addEventListener("click", runEstimate);

        async function loadRegions() {
            try {
                const data = await apiFetch(`/plugins/${PLUGIN}/v1/regions`);
                const zones = data.zones || {};
                const labels = {
                    zone1: "Zone 1 — NA / Europe",
                    zone2: "Zone 2 — Asia Pacific",
                    zone3: "Zone 3 — South America / ME / Africa",
                };
                [sourceEl, targetEl].forEach(sel => {
                    sel.innerHTML = '<option value="">— select region —</option>';
                    for (const [zone, list] of Object.entries(zones)) {
                        const g = document.createElement("optgroup");
                        g.label = labels[zone] || zone;
                        list.sort().forEach(r => {
                            const o = document.createElement("option");
                            o.value = r; o.textContent = r;
                            g.appendChild(o);
                        });
                        sel.appendChild(g);
                    }
                });
                sourceEl.value = "westeurope";
                targetEl.value = "francecentral";
            } catch (e) {
                showError("Failed to load regions: " + e.message);
            }
        }

        async function runEstimate() {
            const src = sourceEl.value;
            const tgt = targetEl.value;
            if (!src || !tgt) { showError("Select both regions."); return; }

            const tbAB = parseFloat(trafficAB.value) || 0;
            const tbBA = parseFloat(trafficBA.value) || 0;
            const isSame = scenarioEl.value === "same-region";

            estimateBtn.disabled = true;
            estimateBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Estimating…';
            showLoading(true);
            hideError();

            try {
                const r = await apiPost(`/plugins/${PLUGIN}/v1/estimate-with-insights`, {
                    source_region: src,
                    target_region: tgt,
                    traffic_ab_gb: tbAB * 1024,
                    traffic_ba_gb: tbBA * 1024,
                    same_region: isSame,
                    currency: "USD",
                });
                displayEstimateResults(r.estimate, tbAB, tbBA, isSame, r.same_region_estimate);
                analysisData = r;
                $("nc-btn-to-step3").disabled = false;
            } catch (e) {
                showError(e.message || "Estimation failed.");
            } finally {
                estimateBtn.disabled = false;
                estimateBtn.innerHTML = '<i class="bi bi-calculator"></i> Estimate Cost';
                showLoading(false);
            }
        }

        function displayEstimateResults(r, tbAB, tbBA, isSame, sameEst) {
            $("nc-estimate-results").style.display = "";
            $("nc-monthly").textContent = fmtUSD(r.monthly_total_usd);
            $("nc-annual").textContent = fmtUSD(r.annual_total_usd);
            $("nc-per-tb").textContent = fmtUSD(r.per_tb_usd);

            // Breakdown
            const tbody = $("nc-breakdown-body");
            tbody.innerHTML = (r.breakdown || []).map(b => `
                <tr>
                    <td>${esc(b.direction)}</td>
                    <td class="text-end">${b.traffic_gb.toLocaleString()} GB</td>
                    <td class="text-end">${fmtUSD(b.outbound_cost)}</td>
                    <td class="text-end">${fmtUSD(b.inbound_cost)}</td>
                    <td class="text-end fw-semibold">${fmtUSD(b.subtotal)}</td>
                </tr>
            `).join("");

            // Badges
            const mb = $("nc-model-badge");
            mb.textContent = r.pricing_model;
            mb.className = "nc-info-badge " + (r.pricing_model === "same-region-vnet-peering" ? "nc-badge-same" : "nc-badge-global");

            $("nc-zones-badge").textContent = r.source_zone === r.target_zone
                ? `Both in ${r.source_zone.toUpperCase()}`
                : `${r.source_zone.toUpperCase()} ↔ ${r.target_zone.toUpperCase()}`;

            const sb = $("nc-source-badge");
            const live = r.pricing_source === "azure-retail-prices-api";
            sb.textContent = live ? "Live pricing" : "Fallback pricing";
            sb.className = "nc-info-badge " + (live ? "nc-badge-live" : "nc-badge-fallback");

            // Notes
            $("nc-notes").innerHTML = (r.notes || [])
                .map(n => `<div class="nc-note"><i class="bi bi-info-circle"></i> ${esc(n)}</div>`)
                .join("");

            // Interpretation
            const totalTB = tbAB + tbBA;
            const interp = $("nc-interpretation");
            if (!isSame && r.monthly_total_usd < 10000) {
                interp.innerHTML = `<i class="bi bi-lightbulb"></i>
                    At <strong>${totalTB.toFixed(1)} TB/month</strong>, global peering costs
                    <strong>${fmtUSD(r.monthly_total_usd)}/month</strong>
                    (${fmtUSD(r.annual_total_usd)}/year) — typically low compared to
                    multi-region deployment value.`;
                interp.style.display = "";
            } else {
                interp.style.display = "none";
            }

            // Delta
            const deltaEl = $("nc-delta");
            if (sameEst && !isSame) {
                const sameCost = sameEst.monthly_total_usd;
                const globalCost = r.monthly_total_usd;
                const diff = globalCost - sameCost;
                $("nc-delta-same").textContent = fmtUSD(sameCost) + "/mo";
                $("nc-delta-global").textContent = fmtUSD(globalCost) + "/mo";
                $("nc-delta-diff").textContent = `+${fmtUSD(diff)}/mo (${sameCost > 0 ? ((diff / sameCost) * 100).toFixed(0) : "—"}% more)`;
                deltaEl.style.display = "";
            } else {
                deltaEl.style.display = "none";
            }
        }

        // ---------------------------------------------------------------
        // Mode 2 — Billing upload
        // ---------------------------------------------------------------
        const billingFile = $("nc-billing-file");
        const billingBtn = $("nc-billing-analyze-btn");

        billingFile.addEventListener("change", () => {
            const name = billingFile.files[0]?.name || "";
            $("nc-billing-file-name").textContent = name;
            billingBtn.disabled = !name;
        });

        // Optional price sheet display
        const priceFile = $("nc-pricesheet-file");
        priceFile.addEventListener("change", () => {
            $("nc-pricesheet-file-name").textContent = priceFile.files[0]?.name || "";
        });

        billingBtn.addEventListener("click", async () => {
            const file = billingFile.files[0];
            if (!file) { showError("Please select a billing CSV file."); return; }

            billingBtn.disabled = true;
            billingBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Analysing…';
            showLoading(true);
            hideError();

            try {
                const fd = new FormData();
                fd.append("usage_file", file);
                const r = await fetch(`/plugins/${PLUGIN}/v1/analyze-billing`, {
                    method: "POST",
                    body: fd,
                });
                if (!r.ok) {
                    const err = await r.json().catch(() => ({}));
                    throw new Error(err.detail || err.message || `HTTP ${r.status}`);
                }
                const data = await r.json();
                displayBillingResults(data.billing);
                analysisData = data;
                $("nc-btn-to-step3").disabled = false;
            } catch (e) {
                showError(e.message || "Billing analysis failed.");
            } finally {
                billingBtn.disabled = false;
                billingBtn.innerHTML = '<i class="bi bi-search"></i> Analyse Billing Data';
                showLoading(false);
            }
        });

        function displayBillingResults(b) {
            $("nc-billing-results").style.display = "";
            $("nc-billing-total").textContent = fmtUSD(b.total_network_cost);
            $("nc-billing-rows").textContent = b.network_rows_found.toLocaleString();
            $("nc-billing-peering").textContent = b.peering_rows_found.toLocaleString();
            $("nc-billing-region").textContent = b.dominant_region || "—";

            // Meter breakdown (top 15)
            const mBody = $("nc-billing-meter-body");
            mBody.innerHTML = (b.meter_breakdown || []).slice(0, 15).map(m => `
                <tr>
                    <td>${esc(m.meter_category)}</td>
                    <td>${esc(m.meter_name || m.meter_sub_category)}</td>
                    <td>${esc(m.region)}</td>
                    <td class="text-end">${m.total_usage.toLocaleString()} ${esc(m.unit)}</td>
                    <td class="text-end fw-semibold">${fmtUSD(m.total_cost)}</td>
                </tr>
            `).join("");

            // Region breakdown
            const rBody = $("nc-billing-region-body");
            rBody.innerHTML = (b.region_breakdown || []).map(r => `
                <tr>
                    <td>${esc(r.region)}</td>
                    <td class="text-end">${fmtUSD(r.total_cost)}</td>
                    <td class="text-end">${fmtUSD(r.peering_cost)}</td>
                    <td class="text-end">${r.meter_count}</td>
                </tr>
            `).join("");

            // Caveats
            $("nc-billing-caveats").innerHTML = (b.caveats || [])
                .map(c => `<div class="nc-caveat"><i class="bi bi-exclamation-triangle"></i> ${esc(c)}</div>`)
                .join("");
        }

        // ---------------------------------------------------------------
        // Mode 3 — Traffic upload
        // ---------------------------------------------------------------
        const trafficFile = $("nc-traffic-file");
        const trafficBtn = $("nc-traffic-analyze-btn");

        trafficFile.addEventListener("change", () => {
            const name = trafficFile.files[0]?.name || "";
            $("nc-traffic-file-name").textContent = name;
            trafficBtn.disabled = !name;
        });

        trafficBtn.addEventListener("click", async () => {
            const file = trafficFile.files[0];
            if (!file) { showError("Please select a traffic CSV file."); return; }

            trafficBtn.disabled = true;
            trafficBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Analysing…';
            showLoading(true);
            hideError();

            try {
                const fd = new FormData();
                fd.append("traffic_file", file);
                const r = await fetch(`/plugins/${PLUGIN}/v1/analyze-traffic`, {
                    method: "POST",
                    body: fd,
                });
                if (!r.ok) {
                    const err = await r.json().catch(() => ({}));
                    throw new Error(err.detail || err.message || `HTTP ${r.status}`);
                }
                const data = await r.json();
                displayTrafficResults(data.traffic);
                analysisData = data;
                $("nc-btn-to-step3").disabled = false;
            } catch (e) {
                showError(e.message || "Traffic analysis failed.");
            } finally {
                trafficBtn.disabled = false;
                trafficBtn.innerHTML = '<i class="bi bi-search"></i> Analyse Traffic Data';
                showLoading(false);
            }
        });

        function displayTrafficResults(t) {
            $("nc-traffic-results").style.display = "";
            const totalTB = t.total_traffic_gb / 1024;
            $("nc-traffic-total").textContent = `${totalTB.toFixed(1)} TB`;
            $("nc-traffic-cost").textContent = fmtUSD(t.total_estimated_cost);
            $("nc-traffic-top-pair").textContent = t.dominant_pair || "—";
            $("nc-traffic-direction").textContent = t.dominant_direction || "—";

            const pBody = $("nc-traffic-pairs-body");
            pBody.innerHTML = (t.top_pairs || []).map(p => `
                <tr>
                    <td>${esc(p.source_region)}</td>
                    <td>${esc(p.target_region)}</td>
                    <td class="text-end">${p.traffic_gb.toLocaleString()}</td>
                    <td class="text-end">${p.rate_per_gb.toFixed(4)}</td>
                    <td class="text-end fw-semibold">${fmtUSD(p.estimated_monthly_cost)}</td>
                </tr>
            `).join("");

            $("nc-traffic-caveats").innerHTML = (t.caveats || [])
                .map(c => `<div class="nc-caveat"><i class="bi bi-exclamation-triangle"></i> ${esc(c)}</div>`)
                .join("");
        }

        // ---------------------------------------------------------------
        // Step 3 — Insights
        // ---------------------------------------------------------------
        function renderInsights(ins) {
            if (!ins) return;
            $("nc-insights-headline").textContent = ins.headline || "";

            // Insight cards
            const grid = $("nc-insights-grid");
            const iconMap = {
                info: "bi-info-circle",
                dollar: "bi-currency-dollar",
                check: "bi-check-circle",
                warning: "bi-exclamation-triangle",
            };
            grid.innerHTML = (ins.insights || []).map(item => `
                <div class="nc-insight-card">
                    <div class="nc-insight-icon nc-icon-${esc(item.icon)}">
                        <i class="bi ${iconMap[item.icon] || "bi-info-circle"}"></i>
                    </div>
                    <div class="nc-insight-title">${esc(item.title)}</div>
                    <div class="nc-insight-value">${esc(item.value)}</div>
                    <div class="nc-insight-desc">${esc(item.description)}</div>
                </div>
            `).join("");

            // Interpretation
            const interp = $("nc-insights-interpretation");
            interp.innerHTML = `<i class="bi bi-lightbulb"></i> ${esc(ins.interpretation || "")}`;

            // Recommendations
            const recs = $("nc-insights-recommendations");
            recs.innerHTML = (ins.recommendations || [])
                .map(r => `<div class="nc-rec-item"><i class="bi bi-arrow-right-circle"></i> ${esc(r)}</div>`)
                .join("");
        }

        // ---------------------------------------------------------------
        // Helpers
        // ---------------------------------------------------------------
        function fmtUSD(v) {
            if (v == null) return "—";
            return "$" + Number(v).toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
            });
        }

        function esc(s) {
            if (typeof escapeHtml === "function") return escapeHtml(String(s || ""));
            const d = document.createElement("div");
            d.textContent = String(s || "");
            return d.innerHTML;
        }

        function showError(msg) {
            const el = $("nc-error");
            el.textContent = msg;
            el.style.display = "";
        }

        function hideError() {
            $("nc-error").style.display = "none";
        }

        function showLoading(v) {
            $("nc-loading").style.display = v ? "" : "none";
        }
    }
})();
