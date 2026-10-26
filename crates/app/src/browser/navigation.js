// Runs before each top-level document's own scripts: marks the document as leaving once a
// navigation away from it starts, and clears the mark when the page takes the navigation over.
(() => {
  // WebView2 runs initialization scripts in frames too; only the top document is read.
  if (window !== window.top || !window.navigation) return;
  navigation.addEventListener("navigate", (event) => {
    if (!event.destination.sameDocument) document.__aesCrawlLeaving = true;
  });
  for (const type of ["navigatesuccess", "navigateerror"]) {
    navigation.addEventListener(type, () => {
      document.__aesCrawlLeaving = false;
    });
  }
})();
