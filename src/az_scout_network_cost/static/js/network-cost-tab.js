/* eslint-disable @microsoft/sdl/no-inner-html -- Dynamic values use escapeHtml(). */
// VNet Peering Cost Estimator — plugin tab logic
// Globals from app.js: apiFetch, apiPost, escapeHtml
(function () {
    const PLUGIN_NAME = "network-cost";
    const container = document.getElementById("plugin-tab-" + PLUGIN_NAME);
    if (!container) return;

    // Load HTML fragment
    fetch(`/plugins/${PLUGIN_NAME}/static/html/network-cost-tab.html`)
        .then(resp => resp.text())
        .then(html => {
            container.innerHTML = html;
            initNetworkCost();
        })
        .catch(err => {
            container.innerHTML = `<div class="alert alert-danger">Failed to load plugin UI: ${err.message}</div>`;
        });

    function initNetworkCost() {
        const sourceEl = document.getElementById("nc-source-region");
        const targetEl = document.getElementById("nc-target-region");
        const trafficAB = document.getElementById("nc-traffic-ab");
        const trafficBA = document.getElementById("nc-traffic-ba");
        const scenarioEl = document.getElementById("nc-scenario");
        const btn = document.getElementById("nc-estimate-btn");
        const resultsEl = document.getElementById("nc-results");
        const errorEl = document.getElementById("nc-error");

        // Populate region selectors from the API
        loadRegions();

        btn.addEventListener("click", runEstimate);

        // ---------------------------------------------------------------
        // Region loading
        // ---------------------------------------------------------------
        async function loadRegions() {
            try {
                const data = await apiFetch(`/plugins/${PLUGIN_NAME}/v1/regions`);
                const zones = data.zones || {};
                const zoneLabels = {
                    zone1: "Zone 1 — NA / Europe / Australia / Japan",
                    zone2: "Zone 2 — Asia Pacific",
                    zone3: "Zone 3 — South America / Middle East / Africa",
                };

                [sourceEl, targetEl].forEach(sel => {
                    sel.innerHTML = '<option value="">— select region —</option>';
                    for (const [zone, regionList] of Object.entries(zones)) {
                        const group = document.createElement("optgroup");
                        group.label = zoneLabels[zone] || zone;
                        regionList.sort().forEach(r => {
                            const opt = document.createElement("option");
                            opt.value = r;
                            opt.textContent = r;
                            group.appendChild(opt);
                        });
                        sel.appendChild(group);
                    }
                });

                // Default selections for a quick demo
                sourceEl.value = "westeurope";
                targetEl.value = "francecentral";
            } catch (e) {
                showError("Failed to load regions: " + e.message);
            }
        }

        // ---------------------------------------------------------------
        // Estimation
        // ---------------------------------------------------------------
        async function runEstimate() {
            const source = sourceEl.value;
            const target = targetEl.value;
            if (!source || !target) {
                showError("Please select both source and target regions.");
                return;
            }

            const tbAB = parseFloat(trafficAB.value) || 0;
            const tbBA = parseFloat(trafficBA.value) || 0;
            const sameRegion = scenarioEl.value === "same-region";

            // Convert TB to GB for the API
            const gbAB = tbAB * 1024;
            const gbBA = tbBA * 1024;

            btn.disabled = true;
            btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Estimating…';
            hideError();

            try {
                const result = await apiPost(`/plugins/${PLUGIN_NAME}/v1/estimate`, {
                    source_region: source,
                    target_region: target,
                    traffic_ab_gb: gbAB,
                    traffic_ba_gb: gbBA,
                    same_region: sameRegion,
                    currency: "USD",
                });
                displayResults(result, tbAB, tbBA, sameRegion, source, target);
            } catch (e) {
                showError(e.message || "Estimation failed.");
                resultsEl.style.display = "none";
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i class="bi bi-calculator"></i> Estimate Cost';
            }
        }

        // ---------------------------------------------------------------
        // Display results
        // ---------------------------------------------------------------
        function displayResults(r, tbAB, tbBA, sameRegion, source, target) {
            resultsEl.style.display = "block";

            // Summary metrics
            document.getElementById("nc-monthly").textContent = formatUSD(r.monthly_total_usd);
            document.getElementById("nc-annual").textContent = formatUSD(r.annual_total_usd);
            document.getElementById("nc-per-tb").textContent = formatUSD(r.per_tb_usd);

            // Breakdown table
            const tbody = document.getElementById("nc-breakdown-body");
            tbody.innerHTML = "";
            (r.breakdown || []).forEach(b => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td>${escapeHtml(b.direction)}</td>
                    <td class="text-end">${b.traffic_gb.toLocaleString()} GB</td>
                    <td class="text-end">${formatUSD(b.outbound_cost)}</td>
                    <td class="text-end">${formatUSD(b.inbound_cost)}</td>
                    <td class="text-end fw-semibold">${formatUSD(b.subtotal)}</td>
                `;
                tbody.appendChild(tr);
            });

            // Pricing model badge
            const modelBadge = document.getElementById("nc-model-badge");
            modelBadge.textContent = r.pricing_model;
            modelBadge.className = "nc-info-badge " +
                (r.pricing_model === "same-region-vnet-peering" ? "nc-badge-same" : "nc-badge-global");

            // Zones badge
            const zonesBadge = document.getElementById("nc-zones-badge");
            zonesBadge.textContent = r.source_zone === r.target_zone
                ? `Both in ${r.source_zone.toUpperCase()}`
                : `${r.source_zone.toUpperCase()} ↔ ${r.target_zone.toUpperCase()}`;

            // Pricing source badge
            const sourceBadge = document.getElementById("nc-source-badge");
            const isLive = r.pricing_source === "azure-retail-prices-api";
            sourceBadge.textContent = isLive ? "Live pricing" : "Fallback pricing";
            sourceBadge.className = "nc-info-badge " + (isLive ? "nc-badge-live" : "nc-badge-fallback");

            // Notes
            const notesEl = document.getElementById("nc-notes");
            notesEl.innerHTML = (r.notes || [])
                .map(n => `<div class="nc-note"><i class="bi bi-info-circle"></i> ${escapeHtml(n)}</div>`)
                .join("");

            // Interpretation message
            const interpEl = document.getElementById("nc-interpretation");
            const totalTB = tbAB + tbBA;
            if (!sameRegion && r.monthly_total_usd < 10000) {
                interpEl.innerHTML = `
                    <i class="bi bi-lightbulb"></i>
                    At <strong>${totalTB.toFixed(1)} TB/month</strong>, global peering costs
                    <strong>${formatUSD(r.monthly_total_usd)}/month</strong>
                    (${formatUSD(r.annual_total_usd)}/year) — typically low compared to
                    the value of multi-region deployment.
                `;
                interpEl.style.display = "block";
            } else {
                interpEl.style.display = "none";
            }

            // Delta comparison: always compute both scenarios
            computeDelta(source, target, tbAB, tbBA, sameRegion, r);
        }

        // ---------------------------------------------------------------
        // Delta comparison (same-region vs global)
        // ---------------------------------------------------------------
        async function computeDelta(source, target, tbAB, tbBA, currentIsSame, currentResult) {
            const deltaEl = document.getElementById("nc-delta");
            try {
                const otherResult = await apiPost(`/plugins/${PLUGIN_NAME}/v1/estimate`, {
                    source_region: source,
                    target_region: target,
                    traffic_ab_gb: tbAB * 1024,
                    traffic_ba_gb: tbBA * 1024,
                    same_region: !currentIsSame,
                    currency: "USD",
                });

                const sameCost = currentIsSame
                    ? currentResult.monthly_total_usd
                    : otherResult.monthly_total_usd;
                const globalCost = currentIsSame
                    ? otherResult.monthly_total_usd
                    : currentResult.monthly_total_usd;
                const diff = globalCost - sameCost;

                document.getElementById("nc-delta-same").textContent = formatUSD(sameCost) + "/mo";
                document.getElementById("nc-delta-global").textContent = formatUSD(globalCost) + "/mo";

                const diffEl = document.getElementById("nc-delta-diff");
                diffEl.textContent = `+${formatUSD(diff)}/mo (${sameCost > 0 ? ((diff / sameCost) * 100).toFixed(0) : "—"}% more)`;

                deltaEl.style.display = "block";
            } catch {
                deltaEl.style.display = "none";
            }
        }

        // ---------------------------------------------------------------
        // Helpers
        // ---------------------------------------------------------------
        function formatUSD(v) {
            if (v == null) return "—";
            return "$" + Number(v).toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
            });
        }

        function showError(msg) {
            errorEl.textContent = msg;
            errorEl.style.display = "block";
        }

        function hideError() {
            errorEl.style.display = "none";
        }
    }
})();
