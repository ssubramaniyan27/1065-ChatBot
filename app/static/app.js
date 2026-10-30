const transcript = document.getElementById("transcript");
const form = document.getElementById("composer");
const input = document.getElementById("question");
const send = document.getElementById("send");
const banner = document.getElementById("banner");
const statusIndex = document.getElementById("status-index");
const statusDate = document.getElementById("status-date");
const statusModel = document.getElementById("status-model");

const history = [];

function setBanner(message, ok = false) {
  if (!message) {
    banner.classList.add("hidden");
    banner.textContent = "";
    return;
  }
  banner.textContent = message;
  banner.classList.toggle("ok", ok);
  banner.classList.remove("hidden");
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderText(value) {
  return escapeHtml(value)
    .replaceAll("\n", "<br>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

async function refreshStatus() {
  try {
    const response = await fetch("/api/status");
    const status = await response.json();
    statusIndex.textContent = status.index_ready
      ? `${status.section_count} sections`
      : "Not built";
    statusDate.textContent = status.ecfr_date || "—";
    statusModel.textContent = status.ollama.model || "Ollama missing";

    if (!status.index_ready) {
      setBanner("Index not built. From this project folder run: python -m app.ingest");
    } else if (!status.ollama.ok) {
      setBanner("Ollama is not running on this PC. Start Ollama and pull llama3.1:8b or llama3.2:3b.");
    } else {
      setBanner("", true);
    }
    return status;
  } catch (error) {
    statusIndex.textContent = "Unreachable";
    setBanner("Could not reach the local app status endpoint.");
    return null;
  }
}

function addBubble(role, html) {
  const article = document.createElement("section");
  article.className = `bubble ${role}`;
  article.innerHTML = html;
  transcript.appendChild(article);
  article.scrollIntoView({ block: "end" });
  return article;
}

function renderSources(sources) {
  if (!sources.length) {
    return `<p class="excerpt">No matching Part 1065 sections were retrieved.</p>`;
  }
  const chips = sources
    .map((source) => {
      const label = escapeHtml(source.citation);
      const href = escapeHtml(source.url);
      return `<a class="cite" href="${href}" target="_blank" rel="noreferrer">${label}</a>`;
    })
    .join("");
  const excerpts = sources
    .slice(0, 3)
    .map(
      (source) =>
        `<p class="excerpt"><strong>${escapeHtml(source.citation)}</strong> — ${escapeHtml(source.excerpt)}</p>`
    )
    .join("");
  return `<div class="sources"><div class="cite-row">${chips}</div>${excerpts}</div>`;
}

async function ask(question) {
  addBubble("user", `<p>${renderText(question)}</p>`);
  const assistant = addBubble("assistant pending", "<p></p>");
  const paragraph = assistant.querySelector("p");
  send.disabled = true;
  input.value = "";

  let answer = "";
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: question, history }),
    });
    if (!response.ok || !response.body) {
      throw new Error(`Chat request failed (${response.status})`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === "sources") {
          assistant.dataset.sources = "ready";
          assistant.insertAdjacentHTML("beforeend", renderSources(event.sources || []));
        } else if (event.type === "token") {
          answer += event.text;
          paragraph.innerHTML = renderText(answer);
          assistant.classList.remove("pending");
          assistant.scrollIntoView({ block: "end" });
        } else if (event.type === "error") {
          assistant.classList.remove("pending");
          assistant.classList.add("error");
          paragraph.textContent = event.message;
        } else if (event.type === "done") {
          assistant.classList.remove("pending");
        }
      }
      if (done) break;
    }
  } catch (error) {
    assistant.classList.remove("pending");
    assistant.classList.add("error");
    paragraph.textContent = error.message || "The local chat request failed.";
  } finally {
    send.disabled = false;
    input.focus();
    if (answer) {
      history.push({ role: "user", content: question });
      history.push({ role: "assistant", content: answer });
    }
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = input.value.trim();
  if (question) ask(question);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll("[data-suggest]").forEach((button) => {
  button.addEventListener("click", () => ask(button.dataset.suggest));
});

refreshStatus();
