// Campo "Outros" das perícias: salva ao apertar Enter ou sair do campo, o servidor recalcula a ficha
// e a página troca os totais sem recarregar. Enquanto salva, a perícia não pode ser rolada.
(() => {
  const lista = document.querySelector("ul.pericias[data-csrf]");
  if (!lista) return;

  const numero = (texto) => (String(texto).trim() === "" ? 0 : Number(texto));

  const atualizar = (p) => {
    const li = lista.querySelector(`li[data-linha="${p.linha}"]`);
    if (!li) return;
    const total = li.querySelector("[data-total]");
    if (total) total.textContent = p.texto;
    const campo = li.querySelector("input.outros");
    if (campo) campo.value = campo.dataset.salvo = p.outros || "";
    const dica = li.querySelector(".dica-poderes");
    if (dica) { dica.textContent = p.poderes_texto; dica.hidden = !p.poderes_texto; }
  };

  lista.addEventListener("keydown", (e) => {
    if (e.target.matches("input.outros") && e.key === "Enter") {
      e.preventDefault();
      e.target.blur();  // o "change" dispara ao sair do campo
    }
  });

  lista.addEventListener("change", async (e) => {
    const campo = e.target;
    if (!campo.matches("input.outros") || numero(campo.value) === numero(campo.dataset.salvo)) return;
    const li = campo.closest("li");
    const rolar = li.querySelector("button.rolar");
    const erro = li.querySelector(".erro-outros");
    li.classList.add("salvando");
    campo.disabled = true;
    if (rolar) rolar.disabled = true;  // nada de rolar com o total antigo
    erro.hidden = true;
    try {
      const corpo = new FormData();
      corpo.append("csrf", lista.dataset.csrf);
      corpo.append("valor", campo.value.trim());
      const r = await fetch(campo.dataset.url, { method: "POST", body: corpo, headers: { "X-Requested-With": "fetch" } });
      const resposta = await r.json().catch(() => ({}));
      if (!r.ok || !resposta.pericias) throw new Error(resposta.erro || "Não foi possível salvar. Tente de novo.");
      resposta.pericias.forEach(atualizar);
    } catch (falha) {
      campo.value = campo.dataset.salvo;  // volta ao valor que está na ficha
      erro.textContent = falha instanceof TypeError ? "Sem conexão com o servidor." : falha.message;
      erro.hidden = false;
    } finally {
      li.classList.remove("salvando");
      campo.disabled = false;
      if (rolar) rolar.disabled = false;
    }
  });
})();
