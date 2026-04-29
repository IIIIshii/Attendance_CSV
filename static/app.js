(function () {
    "use strict";

    const presentEl = document.getElementById("present-count");
    const absentEl = document.getElementById("absent-count");
    const unknownEl = document.getElementById("unknown-count");
    const totalEl = document.getElementById("total-count");
    const searchInput = document.getElementById("search-input");
    const statusFilter = document.getElementById("filter-status");
    const filterClearBtn = document.getElementById("filter-clear");
    const tbody = document.getElementById("member-rows");
    const emptyResult = document.getElementById("empty-result");
    const extraFilters = Array.from(document.querySelectorAll(".filter-extra"));

    function updateCounters(payload) {
        if (presentEl && typeof payload.present_count === "number") {
            presentEl.textContent = payload.present_count;
        }
        if (absentEl && typeof payload.absent_count === "number") {
            absentEl.textContent = payload.absent_count;
        }
        if (unknownEl && typeof payload.unknown_count === "number") {
            unknownEl.textContent = payload.unknown_count;
        }
        if (totalEl && typeof payload.total_count === "number") {
            totalEl.textContent = payload.total_count;
        }
    }

    function setSelectStatusClass(select, status) {
        select.classList.remove("status-0", "status-1", "status-2");
        select.classList.add(`status-${status}`);
    }

    // 出欠 select 変更時の処理
    document.querySelectorAll(".status-select").forEach((select) => {
        select.addEventListener("change", async (event) => {
            const target = event.currentTarget;
            const id = target.dataset.id;
            const newStatus = target.value;
            const row = target.closest("tr");
            const previousStatus = row ? row.dataset.status : "0";

            target.disabled = true;
            try {
                const body = new URLSearchParams({ status: newStatus });
                const res = await fetch(`/api/set/${id}`, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-Requested-With": "fetch",
                    },
                    body,
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                if (row) row.dataset.status = String(data.status);
                setSelectStatusClass(target, data.status);
                updateCounters(data);
                applyFilters();
            } catch (err) {
                console.error(err);
                target.value = previousStatus;
                setSelectStatusClass(target, previousStatus);
                if (row) row.dataset.status = String(previousStatus);
                alert("更新に失敗しました。再度お試しください。");
            } finally {
                target.disabled = false;
            }
        });
    });

    // 全員リセット
    const resetBtn = document.getElementById("reset-btn");
    if (resetBtn) {
        resetBtn.addEventListener("click", async () => {
            if (!confirm("全員の出欠を未確認に戻します。よろしいですか？")) return;
            resetBtn.disabled = true;
            try {
                const res = await fetch("/api/reset", { method: "POST" });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                document.querySelectorAll("tr[data-status]").forEach((row) => {
                    row.dataset.status = "0";
                });
                document.querySelectorAll(".status-select").forEach((sel) => {
                    sel.value = "0";
                    setSelectStatusClass(sel, "0");
                });
                updateCounters(data);
                applyFilters();
            } catch (err) {
                console.error(err);
                alert("リセットに失敗しました。");
            } finally {
                resetBtn.disabled = false;
            }
        });
    }

    // 検索 + フィルタ
    function applyFilters() {
        if (!tbody) return;
        const searchText = searchInput ? searchInput.value.trim().toLowerCase() : "";
        const wantStatus = statusFilter ? statusFilter.value : "";
        const wantExtras = extraFilters
            .filter((s) => s.value !== "")
            .map((s) => ({ col: s.dataset.col, value: s.value }));

        let visible = 0;
        const rows = tbody.querySelectorAll("tr");
        rows.forEach((row) => {
            let show = true;

            if (wantStatus && row.dataset.status !== wantStatus) show = false;

            if (show) {
                for (const f of wantExtras) {
                    const cell = row.querySelector(
                        `td[data-col="${cssEscape(f.col)}"]`
                    );
                    if (!cell || cell.textContent.trim() !== f.value) {
                        show = false;
                        break;
                    }
                }
            }

            if (show && searchText) {
                // td.textContent を全て連結（出欠 select の option ラベルも入る）
                const text = row.textContent.toLowerCase();
                if (!text.includes(searchText)) show = false;
            }

            row.classList.toggle("row-hidden", !show);
            if (show) visible++;
        });

        if (emptyResult) emptyResult.hidden = visible !== 0;
    }

    function cssEscape(s) {
        if (window.CSS && typeof window.CSS.escape === "function") {
            return window.CSS.escape(s);
        }
        return String(s).replace(/(["\\])/g, "\\$1");
    }

    if (searchInput) searchInput.addEventListener("input", applyFilters);
    if (statusFilter) statusFilter.addEventListener("change", applyFilters);
    extraFilters.forEach((s) => s.addEventListener("change", applyFilters));
    if (filterClearBtn) {
        filterClearBtn.addEventListener("click", () => {
            if (searchInput) searchInput.value = "";
            if (statusFilter) statusFilter.value = "";
            extraFilters.forEach((s) => (s.value = ""));
            applyFilters();
        });
    }
})();
