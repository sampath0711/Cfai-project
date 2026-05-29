function confirmDelete() {
    return confirm("Are you sure you want to delete this room?");
}

function toggleTheme() {
    const html = document.documentElement;
    const current = html.getAttribute("data-theme") || "light";
    const next = current === "light" ? "dark" : "light";
    html.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
}

function toggleChat() {
    const box = document.getElementById("chatWindow");
    if (box) box.classList.toggle("open");
}

async function askBot() {
    const input = document.getElementById("chatQuestion");
    const messages = document.getElementById("chatMessages");
    if (!input || !messages || !input.value.trim()) return;

    const q = input.value.trim();
    messages.innerHTML += `<p><b>You:</b> ${q}</p>`;
    input.value = "";

    const res = await fetch("/chatbot", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({question: q})
    });
    const data = await res.json();
    messages.innerHTML += `<p><b>Bot:</b> ${data.answer}</p>`;
    messages.scrollTop = messages.scrollHeight;
}

document.addEventListener("DOMContentLoaded", () => {
    setTimeout(() => {
        const loader = document.getElementById("loader");
        if (loader) loader.classList.add("hide");
    }, 550);

    const panels = document.querySelectorAll(".glass-panel");
    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (entry.isIntersecting) entry.target.classList.add("show-panel");
        });
    }, { threshold: 0.12 });

    panels.forEach((panel, index) => {
        panel.style.transitionDelay = `${Math.min(index * 50, 250)}ms`;
        observer.observe(panel);
    });
});
