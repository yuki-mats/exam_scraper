(() => {
  const link = document.querySelector("#batch-monitor-link");
  const qualificationSelect = document.querySelector("#qualification-select");
  if (!(link instanceof HTMLAnchorElement)) return;

  const updateLink = () => {
    const requested = new URLSearchParams(window.location.search).get("qualification") || "";
    const selected = qualificationSelect instanceof HTMLSelectElement
      ? qualificationSelect.value
      : "";
    const qualification = selected || requested;
    const query = new URLSearchParams();
    if (qualification) query.set("qualification", qualification);
    link.href = qualification ? `/monitor?${query.toString()}` : "/monitor";
  };

  qualificationSelect?.addEventListener("change", updateLink);
  if (qualificationSelect instanceof HTMLSelectElement) {
    new MutationObserver(updateLink).observe(qualificationSelect, {
      childList: true,
      attributes: true,
      attributeFilter: ["value"],
    });
  }
  window.addEventListener("popstate", updateLink);
  updateLink();
})();
