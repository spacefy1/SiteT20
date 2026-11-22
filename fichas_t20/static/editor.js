// Editor da ficha: envia só os campos alterados e busca opções das listas enormes no servidor.
(() => {
  const form = document.getElementById("editor");
  if (!form) return;

  const valor = (el) => (el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value.trim());

  form.addEventListener("submit", () => {
    form.querySelectorAll("[data-orig]").forEach((el) => {
      if (valor(el) !== el.dataset.orig) return;
      el.disabled = true;  // campo desabilitado não vai no POST
      if (el.type === "checkbox") el.previousElementSibling.disabled = true;  // o "0" escondido do par
    });
  });
  // Voltar pelo navegador reaproveita a página: reabilita o que o envio desligou.
  addEventListener("pageshow", () => form.querySelectorAll(":disabled").forEach((el) => { el.disabled = false; }));

  // Raça: a herança só vale para Moreau; trocar a raça avisa que as escolhas atuais serão apagadas ao salvar.
  const raca = form.elements.namedItem("PJ!S2");
  if (raca) {
    raca.addEventListener("input", () => {
      document.querySelector("label.heranca").hidden = raca.value !== "Moreau";
      const trocou = raca.value !== raca.dataset.orig;
      document.querySelector(".troca-raca").hidden = !trocou;
      document.querySelectorAll(".escolhas-raca, .resumo-raca").forEach((el) => { el.hidden = trocou; });
    });
  }

  // Poderes: a descrição vem do BD CATÁLOGO assim que o jogador escolhe; o aviso só depois de salvar.
  form.querySelectorAll("table[data-descricao]").forEach((tabela) => {
    tabela.addEventListener("input", (e) => {
      const el = e.target;
      if (!el.name.startsWith("Poderes escolhidos!B")) return;
      clearTimeout(el.esperaDescricao);
      el.esperaDescricao = setTimeout(async () => {
        const alvo = el.closest("tr").querySelector(".descricao-poder");
        const opcao = el.value.trim();
        if (!opcao) { alvo.replaceChildren(); return; }
        try {
          const r = await fetch(`${tabela.dataset.descricao}?opcao=${encodeURIComponent(opcao)}`, { headers: { "X-Requested-With": "fetch" } });
          if (el.value.trim() !== opcao) return;  // o jogador já digitou outra coisa
          if (r.status === 204) alvo.replaceChildren();
          else if (r.ok) alvo.innerHTML = await r.text();  // HTML montado (e escapado) pelo servidor
        } catch { /* sem conexão: a descrição aparece depois de salvar */ }
      }, 350);
    });
  });

  let espera;
  form.querySelectorAll("input[data-busca]").forEach((el) => {
    el.addEventListener("input", () => {
      clearTimeout(espera);
      espera = setTimeout(async () => {
        const termo = el.value.trim();
        if (termo.length < 2) return;
        try {
          const r = await fetch(`${el.dataset.busca}?q=${encodeURIComponent(termo)}`, { headers: { "X-Requested-With": "fetch" } });
          if (!r.ok) return;
          const lista = document.getElementById(el.getAttribute("list"));
          lista.replaceChildren(...(await r.json()).map((texto) => Object.assign(document.createElement("option"), { value: texto })));
        } catch { /* sem conexão: o campo continua aceitando texto */ }
      }, 200);
    });
  });
})();
