const initPubDateButtons = () => {
  // check if form instantiation is to create new post or edit existing one
  const isCreateOp = {{ form.initial|yesno:"false,true" }};

  const pubDateElem = document.querySelector('input[name="published_at"]');
  if (pubDateElem.value === "") {
    // add 'set to today' functionality on publication date
    const setTodaySpan = document.getElementById("set-today");
    const setTodayAnchor = document.createElement("a");
    setTodayAnchor.innerText = "set to today";
    setTodayAnchor.href = "javascript:";
    setTodaySpan.appendChild(document.createTextNode(" — "));
    setTodaySpan.appendChild(setTodayAnchor);
    setTodaySpan.addEventListener("click", () => {
      const isoDate = new Date().toISOString().substring(0, 10);
      document.querySelector('input[name="published_at"]').value = isoDate;
    });
  } else if (isCreateOp) {
    // add 'make draft / set to empty' functionality
    const setEmptySpan = document.getElementById("set-empty");
    const setEmptyAnchor = document.createElement("a");
    setEmptyAnchor.innerText = "set as draft";
    setEmptyAnchor.href = "javascript:";
    setEmptySpan.appendChild(document.createTextNode(" — "));
    setEmptySpan.appendChild(setEmptyAnchor);
    setEmptySpan.addEventListener("click", () => {
      document.querySelector('input[name="published_at"]').value = "";
    });
  }
};

// init
initPubDateButtons();
