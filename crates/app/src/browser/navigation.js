(() => {
  if (window !== window.top) return;
  if (document.__aesCrawlLeaving === undefined) {
    document.__aesCrawlLeaving = false;
    window.navigation?.addEventListener("navigate", (event) => {
      if (!event.destination.sameDocument) document.__aesCrawlLeaving = true;
    });
    for (const event of ["navigatesuccess", "navigateerror"]) {
      window.navigation?.addEventListener(event, () => {
        document.__aesCrawlLeaving = false;
      });
    }
  }
})();
