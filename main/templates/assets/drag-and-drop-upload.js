// setup for drag and drop uploading
document.getElementById("js-show").style.display = "inline";
document.getElementById("js-status").style.color = "#f00";

// get body element, used for drag and drop onto it
const bodyElem = document.querySelector('textarea[name="body"]');

// prevent default drag and drop behaviours
["drag", "dragstart", "dragend", "dragover", "dragenter", "dragleave", "drop"].forEach((event) => {
  bodyElem.addEventListener(event, (e) => {
    e.preventDefault();
    e.stopPropagation();
  });
});

const injectImageMarkdown = (textInputElem, imageName, imageURL) => {
  // build markdown image code
  const markdownImageCode = `![${imageName}](${imageURL})`;

  // inject markdown image code in cursor position
  if (textInputElem.selectionStart || textInputElem.selectionStart === 0) {
    const startPos = textInputElem.selectionStart;
    const endPos = textInputElem.selectionEnd;
    textInputElem.value =
      textInputElem.value.substring(0, startPos) +
      markdownImageCode +
      "\n" +
      textInputElem.value.substring(endPos, textInputElem.value.length);

    // set cursor location to after markdownImageCode +1 for the new line
    textInputElem.selectionEnd = endPos + markdownImageCode.length + 1;
  } else {
    // there is no cursor, just append
    textInputElem.value += markdownImageCode;
  }
};

const uploadFile = async (file) => {
  // prepare form data
  const formData = new FormData();
  const name = file.name;
  formData.append("file", file);

  // disable textarea and show status message
  bodyElem.disabled = true;
  document.getElementById("js-status").innerText = "UPLOADING...";

  try {
    const response = await fetch("/images/?raw=true", {
      method: "POST",
      headers: {
        "X-CSRFToken": "{{ csrf_token }}",
      },
      body: formData,
    });

    if (response.ok) {
      // success, inject markdown snippet
      injectImageMarkdown(bodyElem, name, response.url);
    } else {
      const errorText = await response.text();
      alert(`Image could not be uploaded. ${errorText}`);
    }
  } catch (error) {
    alert(`Image could not be uploaded. ${error.message}`);
  } finally {
    // re-enable textarea
    bodyElem.disabled = false;
    // update status message
    document.getElementById("js-status").innerText = "";
  }
};

bodyElem.addEventListener("drop", (e) => {
  // only upload one file at a time
  if (e.dataTransfer.files.length === 1) {
    uploadFile(e.dataTransfer.files[0]);
  }
});

bodyElem.addEventListener("paste", (event) => {
  // use event.originalEvent.clipboard for newer chrome versions
  const items = (event.clipboardData || event.originalEvent.clipboardData).items;

  // find pasted image among pasted items
  let blob = null;
  for (const item of items) {
    if (item.type.indexOf("image") === 0) {
      blob = item.getAsFile();
    }
  }

  // load image if there is a pasted image
  if (blob !== null) {
    uploadFile(blob);
  }
});
