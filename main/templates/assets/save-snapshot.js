// keep timeout ids in an array so we reset them
let timeoutIds = [];

// save post title and body as a Snapshot connected to current user
const saveLogEntry = async () => {
  console.log("saving...");
  let title = document.getElementById("id_title").value;
  if (!title) {
    title = "Untitled";
  }
  const body = document.getElementById("id_body").value;

  // prepare form data
  const formData = new FormData();
  formData.append("title", title);
  formData.append("body", body);

  try {
    const response = await fetch("/post-backups/create/", {
      method: "POST",
      headers: {
        "X-CSRFToken": "{{ csrf_token }}",
      },
      body: formData,
    });

    if (response.ok) {
      console.log("success");
      // success, show feedback
    } else {
      console.log("failure");
      // failure, show feedback
    }
  } catch (error) {
    console.log("failure", error);
  }
};

// clear timeout ids from given array
const clearTimeoutList = (timeoutList) => {
  timeoutList.forEach((timeoutId) => {
    clearTimeout(timeoutId);
  });
};

// listen for body textarea changes
const initAutoSave = () => {
  document.getElementById("id_body").addEventListener("keyup", () => {
    clearTimeoutList(timeoutIds);
    const timeoutId = setTimeout(saveLogEntry, 2500);
    timeoutIds.push(timeoutId);
  });
};

// init
initAutoSave();
