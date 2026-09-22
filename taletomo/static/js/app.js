// TaleTomo Island Controllers (Vue 3 vendored)

document.addEventListener("DOMContentLoaded", () => {
  // 1. Job Poller Component
  const jobEl = document.getElementById("job-poller");
  if (jobEl && window.Vue) {
    const { createApp, ref, onMounted } = window.Vue;
    createApp({
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
          if (["ready", "failed", "cancelled"].includes(status.value)) return;
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
              } else if (!["ready", "failed", "cancelled"].includes(data.status)) {
                setTimeout(poll, 1500);
              }
            }
          } catch (e) {
            console.error("Poll failed", e);
            setTimeout(poll, 3000);
          }
        };

        onMounted(() => {
          if (!["ready", "failed", "cancelled"].includes(status.value)) {
            setTimeout(poll, 1000);
          }
        });

        return { status, stage, progress, resultUrl, errorMessage, tokens, cost };
      },
    }).mount("#job-poller");
  }

  // 2. Tomo Assistant Drawer
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

  // 3. Manuscript Word Count Counter
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
