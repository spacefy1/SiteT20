(() => {
  const feed = document.querySelector(".feed");
  const painel = document.querySelector(".chat details");
  const form = document.getElementById("rolagem");
  const erro = document.getElementById("erro-rolagem");
  if (!feed) return;
  let ultimo = Number(feed.dataset.ultimo) || 0;

  const horaLocal = (raiz) => raiz.querySelectorAll("time").forEach((t) => {
    t.textContent = new Date(t.dateTime).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  });
  const adicionar = (html) => {
    const molde = document.createElement("template");
    molde.innerHTML = html;
    const novas = [...molde.content.querySelectorAll("li[data-id]")]
      .filter((li) => !feed.querySelector(`[data-id="${li.dataset.id}"]`));
    if (!novas.length) return;
    feed.querySelector(".vazio-chat")?.remove();
    novas.forEach((li) => { horaLocal(li); feed.append(li); ultimo = Math.max(ultimo, Number(li.dataset.id)); });
    feed.scrollTop = feed.scrollHeight;
  };

  horaLocal(feed);
  feed.scrollTop = feed.scrollHeight;
  if (matchMedia("(max-width: 860px)").matches) painel.open = false;

  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    erro.hidden = true;
    try {
      const r = await fetch(form.action, { method: "POST", body: new FormData(form, e.submitter), headers: { "X-Requested-With": "fetch" } });
      if (r.status === 401) return location.assign("/login");
      const texto = await r.text();
      if (!r.ok) { erro.textContent = texto; erro.hidden = false; painel.open = true; return; }
      painel.open = true;
      adicionar(texto);
    } catch {
      erro.textContent = "Sem conexão com o servidor. Tente rolar de novo."; erro.hidden = false; painel.open = true;
    }
  });

  setInterval(async () => {
    if (document.hidden) return;
    try {
      const r = await fetch(`${feed.dataset.url}?depois=${ultimo}`, { headers: { "X-Requested-With": "fetch" } });
      if (r.status === 401) return location.assign("/login");
      if (r.ok) adicionar(await r.text());
    } catch { /* tenta de novo no próximo ciclo */ }
  }, 3000);
})();
