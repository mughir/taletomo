// TaleTomo Island Controllers (Vue 3 vendored) + motion choreography

document.addEventListener("DOMContentLoaded", () => {
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // 1. Scroll reveal choreography — transform/opacity only, IntersectionObserver-driven
  if (!prefersReducedMotion && "IntersectionObserver" in window) {
    const revealTargets = document.querySelectorAll(
      [
        ".main-container > section",
        ".main-container > div:not(.messages-container)",
        ".main-container > form",
        ".page-intro",
        ".hero-band",
        ".empty-state",
        ".dashboard-grid > *",
        ".bento > *",
        ".library-grid > *",
        ".form-layout > *",
        ".split-grid > *",
        ".duo-grid > *",
        ".card",
        ".table-wrap",
        ".project-row",
        ".activity-item",
        ".item-card",
        ".subnav",
        ".writer-header",
        ".writer-layout > *",
      ].join(", ")
    );

    revealTargets.forEach((el) => {
      if (el.closest(".mobile-menu") || el.classList.contains("reveal")) return;
      el.classList.add("reveal");
      const siblings = el.parentElement
        ? Array.from(el.parentElement.children).filter((c) => c.classList.contains("reveal"))
        : [];
      const indexInParent = siblings.indexOf(el);
      el.style.setProperty("--reveal-i", Math.min(indexInParent < 0 ? 0 : indexInParent, 8));
    });

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-inview");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.08, rootMargin: "0px 0px -6% 0px" }
    );
    revealTargets.forEach((el) => observer.observe(el));
  }

  // 2. Fluid island menu — hamburger morphs, overlay reveals with stagger
  const toggle = document.querySelector(".nav-toggle");
  const menu = document.getElementById("mobile-menu");
  if (toggle && menu) {
    let closeTimer = null;
    const setOpen = (open) => {
      document.body.classList.toggle("menu-open", open);
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
      if (open) {
        clearTimeout(closeTimer);
        menu.hidden = false;
      } else {
        closeTimer = setTimeout(() => {
          menu.hidden = true;
        }, 650);
      }
    };
    toggle.addEventListener("click", () => {
      setOpen(!document.body.classList.contains("menu-open"));
    });
    menu.addEventListener("click", (event) => {
      if (event.target.closest("a") || event.target.closest("button")) setOpen(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && document.body.classList.contains("menu-open")) {
        setOpen(false);
        toggle.focus();
      }
    });
  }

  // 3. Job Poller Component
  const jobEl = document.getElementById("job-poller");
  if (jobEl && window.Vue) {
    const { createApp, ref, onMounted } = window.Vue;
    const app = createApp({
      delimiters: ["[[", "]]"],
      setup() {
        const jobId = jobEl.dataset.jobId;
        const status = ref(jobEl.dataset.initialStatus);
        const stage = ref(jobEl.dataset.initialStage);
        const progress = ref(parseInt(jobEl.dataset.initialProgress || "0"));
        const resultUrl = ref(jobEl.dataset.initialResultUrl || "");
        const errorMessage = ref(jobEl.dataset.initialError || "");
        const tokens = ref(parseInt(jobEl.dataset.initialTokens || "0"));
        const cost = ref(jobEl.dataset.initialCost || "0.00");

        const poll = async () => {
          if (["ready", "failed", "cancelled", "stale"].includes(status.value)) return;
          try {
            const res = await fetch(`/jobs/${jobId}/status/`);
            if (res.ok) {
              const data = await res.json();
              status.value = data.status;
              stage.value = data.stage;
              progress.value = data.progress_pct;
              resultUrl.value = data.result_url;
              errorMessage.value = data.error_message;
              tokens.value = data.confirmed_tokens;
              cost.value = data.confirmed_cost;

              if (data.status === "ready" && data.result_url) {
                // Auto redirect or show clear CTA
                window.location.href = data.result_url;
              } else if (!["ready", "failed", "cancelled", "stale"].includes(data.status)) {
                setTimeout(poll, 1500);
              }
            }
          } catch (e) {
            console.error("Poll failed", e);
            setTimeout(poll, 3000);
          }
        };

        onMounted(() => {
          if (!["ready", "failed", "cancelled", "stale"].includes(status.value)) {
            setTimeout(poll, 1000);
          }
        });

        return { status, stage, progress, resultUrl, errorMessage, tokens, cost };
      },
    });
    if (app.config && app.config.compilerOptions) {
      app.config.compilerOptions.delimiters = ["[[", "]]"];
    }
    app.mount("#job-poller");
  }

  // 4. Tomo Assistant Drawer
  const tomoEl = document.getElementById("tomo-drawer");
  if (tomoEl && window.Vue) {
    const { createApp, ref } = window.Vue;
    createApp({
      setup() {
        const activeTab = ref("checks");
        return { activeTab };
      },
    }).mount("#tomo-drawer");
  }

  // 5. Manuscript Word Count Counter
  const textarea = document.getElementById("prose-textarea");
  const counter = document.getElementById("word-count-display");
  if (textarea && counter) {
    const updateCount = () => {
      const words = textarea.value.trim().split(/\s+/).filter(Boolean).length;
      counter.textContent = `${words} words`;
    };
    textarea.addEventListener("input", updateCount);
    updateCount();
  }
});

// 6. Style-dictionary chip pickers (multi-select for genre / subgenre / tone).
// Composes the submitted value ("Fantasy / Xianxia", "Grim, Mysterious"),
// suggests dictionary terms with their definitions, and accepts free-typed
// custom terms — Enter or comma commits, Backspace removes the last chip.
document.addEventListener("DOMContentLoaded", () => {
  const pickers = document.querySelectorAll(".style-picker[data-field]");
  if (!pickers.length) return;

  let allTerms = {};
  const termsSource = document.getElementById("style-terms-json");
  if (termsSource) {
    try {
      allTerms = JSON.parse(termsSource.textContent);
    } catch (e) {
      console.error("Style terms JSON is invalid", e);
    }
  }

  const { createApp, ref, computed } = window.Vue;

  const pickerTemplate = `
    <div class="style-picker-box" :class="{ 'is-open': open && suggestions.length }">
      <span v-for="item in selected" :key="item" class="style-chip">
        [[ item ]]
        <button type="button" class="style-chip-remove" @click="removeTerm(item)" :aria-label="'Remove ' + item">&times;</button>
      </span>
      <input class="style-picker-input" type="text" v-model="query"
        :placeholder="selected.length ? '' : placeholder"
        @focus="open = true" @input="open = true" @blur="onBlur" @keydown="onKeydown"
        :aria-label="fieldLabel" autocomplete="off">
      <input type="hidden" :name="hiddenName" :value="hiddenValue">
      <ul v-show="open && suggestions.length" class="style-suggestions" role="listbox">
        <li v-for="s in suggestions" :key="s.name">
          <button type="button" class="style-suggestion" role="option"
            @mousedown.prevent="addTerm(s.name)" :title="s.definition">
            <strong>[[ s.name ]]</strong><span>[[ s.definition ]]</span>
          </button>
        </li>
      </ul>
      <p v-if="open && query && !suggestions.length" class="style-suggestions-note">
        Press Enter to use &ldquo;[[ query ]]&rdquo; as a custom term
      </p>
    </div>
  `;

  pickers.forEach((el) => {
    const field = el.dataset.field;
    // A picker may merge several dictionary axes into one suggestion list
    // (e.g. protagonist archetypes + protagonist traits).
    const fieldKeys = (el.dataset.termsFields || field).split(",").map((k) => k.trim()).filter(Boolean);
    const terms = fieldKeys.flatMap((key) => allTerms[key] || []);
    const initial = (el.dataset.initial || "")
      .split(/[\/,]/)
      .map((part) => part.trim())
      .filter(Boolean);

    createApp({
      delimiters: ["[[" , "]]"],
      setup() {
        const selected = ref(initial.slice());
        const query = ref("");
        const open = ref(false);
        const separator = el.dataset.separator || ", ";
        const hiddenName = el.dataset.hiddenName || field;
        const fieldLabel = el.dataset.label || field;
        const placeholder = el.dataset.placeholder || "Pick or type…";

        const suggestions = computed(() => {
          const q = query.value.trim().toLowerCase();
          return terms
            .filter((t) => !selected.value.some((s) => s.toLowerCase() === t.name.toLowerCase()))
            .filter((t) => !q || t.name.toLowerCase().includes(q) || t.definition.toLowerCase().includes(q))
            .slice(0, 8);
        });

        const hiddenValue = computed(() => selected.value.join(separator));

        const addTerm = (name) => {
          const clean = String(name || "").trim();
          if (!clean) return;
          if (!selected.value.some((s) => s.toLowerCase() === clean.toLowerCase())) {
            selected.value.push(clean);
          }
          query.value = "";
          open.value = true;
        };

        const removeTerm = (name) => {
          selected.value = selected.value.filter((s) => s !== name);
        };

        const onBlur = () => {
          if (query.value.trim()) addTerm(query.value);
          open.value = false;
        };

        const onKeydown = (event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            addTerm(query.value);
          } else if (event.key === ",") {
            event.preventDefault();
            addTerm(query.value);
          } else if (event.key === "Backspace" && !query.value && selected.value.length) {
            selected.value.pop();
          } else if (event.key === "Escape") {
            open.value = false;
          }
        };

        return {
          selected, query, open, suggestions, hiddenValue, hiddenName,
          fieldLabel, placeholder, addTerm, removeTerm, onBlur, onKeydown,
        };
      },
      template: pickerTemplate,
    }).mount(el);
  });
});
