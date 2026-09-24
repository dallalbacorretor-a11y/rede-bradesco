/* pdf-rede.js - monta o PDF da rede e baixa direto, sem janela de impressao.
   O mesmo arquivo nas duas redes (rede-amil e rede-bradesco): cada pagina
   descreve o documento (capa, leitura, blocos) e aqui ele vira PDF pelo
   pdfmake, com as fontes do site. A4 deitado, compacto.

   PDFRede.baixar(doc) -> Promise
   doc = { arquivo, rodape, corretora, corretorNome,
           capa: { titulo, destaque, onde, recorte, placar:[[n, rotulo]],
                   cores:[hex], assinatura },
           secoes: [ {tipo:"leitura", cartoes:[{titulo, etiquetas:[[nome, cor]],
                                                  texto:[[txt, negrito]]}],
                      aviso:[[txt, negrito]]},
                     {tipo:"bloco", titulo, conta, endereco:bool, telefone:bool,
                      produtos:[{nome, cor}],
                      linhas:[{nome, tag, detalhe:[[txt, negrito]], esp,
                               end, tel, bairro, cidade,
                               marcas:[true | false | "só enf"]}]},
                     {tipo:"texto", titulo, conta, texto:[[txt, negrito]]},
                     {tipo:"grade", titulo, conta, cor, itens:[{nome, sub}]} ] } */
(function (global) {
  "use strict";

  var FUNDO = "#0f1a29", OURO = "#c9a227", OURO_ESCURO = "#9a7420",
      TINTA = "#0f1a29", TEXTO = "#3c4a5c", CINZA = "#4a5a6f",
      CINZA_CLARO = "#8695a9", CINZA_CAPA = "#93a2b8", FIO_CAPA = "#3a4658",
      RISCO = "#e6ebf2", BORDA = "#dde3ec", ZEBRA = "#f7f9fc", CARTAO = "#fbfcfd",
      AVISO_FUNDO = "#faf6ea", AVISO_TEXTO = "#5b4a22", NAO = "#ccd4e0";

  var L = 841.89, A = 595.28, M = 31, UTIL = L - 2 * M;

  // --------------------------------------------------------------- carga
  /* pdfmake e as fontes so carregam no primeiro PDF: a pagina abre leve. */
  var base = (function () {
    var s = document.currentScript && document.currentScript.src;
    return s ? s.replace(/[^\/]*$/, "") : "";
  })();
  var pronto = null;
  function carregar() {
    if (pronto) return pronto;
    function script(src) {
      return new Promise(function (ok, erro) {
        var s = document.createElement("script");
        s.src = base + src;
        s.onload = ok;
        s.onerror = function () { erro(new Error("não carregou " + src)); };
        document.head.appendChild(s);
      });
    }
    pronto = script("pdfmake.min.js").then(function () {
      return script("fontes-pdf.js");
    }).then(function () {
      var pm = global.pdfMake, f = global.FONTES_PDF;
      if (pm.addVirtualFileSystem) pm.addVirtualFileSystem(f.vfs); else pm.vfs = f.vfs;
      if (pm.addFonts) pm.addFonts(f.fonts); else pm.fonts = f.fonts;
      return pm;
    });
    pronto.catch(function () { pronto = null; });
    return pronto;
  }

  // ------------------------------------------------------------ utilidades
  function trechos(runs, corNegrito) {
    return (runs || []).map(function (r) {
      return r[1] ? { text: r[0], bold: true, color: corNegrito || TINTA } : { text: r[0] };
    });
  }

  function clara(hex) {
    var n = parseInt(hex.replace("#", ""), 16);
    return "#" + [16, 8, 0].map(function (s) {
      var c = (n >> s) & 255;
      return ("0" + Math.round(c + (255 - c) * 0.86).toString(16)).slice(-2);
    }).join("");
  }

  function visto(cor) {
    return '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="9" viewBox="0 0 11 9">' +
      '<path d="M1.2 4.8 L4 7.6 L9.8 1.4" fill="none" stroke="' + cor +
      '" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  }

  function linhaCanvas(larg, cor, esp) {
    return { canvas: [{ type: "line", x1: 0, y1: 0, x2: larg, y2: 0, lineWidth: esp, lineColor: cor }] };
  }

  // ------------------------------------------------------------------ capa
  function capa(c) {
    var placar = c.placar || [], linhas = [];
    for (var i = 0; i < placar.length; i += 6) {
      var fatia = placar.slice(i, i + 6);
      while (fatia.length < 6) fatia.push(null);
      linhas.push(fatia.map(function (p) {
        if (!p) return { text: "" };
        return { stack: [
          linhaCanvas(92, FIO_CAPA, 0.8),
          { text: typeof p[0] === "number" ? p[0].toLocaleString("pt-BR") : String(p[0]),
            font: "Newsreader", fontSize: 22, color: "#ffffff", margin: [0, 6, 0, 0] },
          { text: String(p[1]).toUpperCase(), fontSize: 6.6, bold: true, color: CINZA_CAPA,
            characterSpacing: 0.9, margin: [0, 2, 0, 12] }
        ] };
      }));
    }
    return [
      { text: (c.selo || "MAZZA BROKER").toUpperCase(), fontSize: 8.5, bold: true, color: OURO,
        characterSpacing: 3, margin: [26, 18, 0, 0] },
      { canvas: [{ type: "line", x1: 26, y1: 8, x2: 100, y2: 8, lineWidth: 1.2, lineColor: OURO }] },
      { text: c.titulo, font: "Newsreader", fontSize: 40, color: "#ffffff", margin: [26, 26, 0, 0] },
      { text: c.destaque, font: "Newsreader", italics: true, fontSize: 40, color: OURO,
        margin: [26, -4, 0, 0] },
      { text: c.onde || "", fontSize: 19, bold: true, color: "#ffffff", margin: [26, 22, 0, 0] },
      c.recorte ? { text: c.recorte, fontSize: 9.5, color: CINZA_CAPA, margin: [26, 3, 0, 0] } : "",
      linhas.length ? { margin: [26, 26, 0, 0], layout: "noBorders",
                        table: { widths: [104, 104, 104, 104, 104, 104], body: linhas } } : "",
      { text: c.assinatura || "", fontSize: 7.4, color: "#7f8ea5", margin: [26, 4, 0, 0],
        pageBreak: "after" }
    ];
  }

  // --------------------------------------------------------------- leitura
  function leitura(s) {
    var cartoes = s.cartoes.map(function (c) {
      var corpo = [{ text: c.titulo.toUpperCase(), fontSize: 7.8, bold: true, color: OURO_ESCURO,
                     characterSpacing: 1, margin: [0, 0, 0, 7] }];
      if (c.etiquetas && c.etiquetas.length) {
        var et = [];
        c.etiquetas.forEach(function (e) {
          et.push({ text: " " + e[0] + " ", bold: true, color: e[1], background: clara(e[1]) });
          et.push({ text: "  " });
        });
        corpo.push({ text: et, fontSize: 8.4, margin: [0, 0, 0, 6], lineHeight: 1.3 });
      }
      corpo.push({ text: trechos(c.texto), fontSize: 8.6, color: TEXTO, lineHeight: 1.35 });
      return { layout: {
          hLineWidth: function (i) { return i === 0 ? 2 : 0.6; },
          hLineColor: function (i) { return i === 0 ? OURO : BORDA; },
          vLineWidth: function () { return 0.6; }, vLineColor: function () { return BORDA; },
          fillColor: function () { return CARTAO; },
          paddingLeft: function () { return 12; }, paddingRight: function () { return 12; },
          paddingTop: function () { return 11; }, paddingBottom: function () { return 11; } },
        table: { widths: ["*"], body: [[{ stack: corpo }]] } };
    });
    var saida = [
      { text: "Como ler este documento", font: "Newsreader", fontSize: 21, color: TINTA,
        margin: [0, 4, 0, 12] },
      { columns: cartoes, columnGap: 14 }
    ];
    if (s.aviso) {
      saida.push({ margin: [0, 16, 0, 0], layout: {
          hLineWidth: function () { return 0; },
          vLineWidth: function (i) { return i === 0 ? 3 : 0; },
          vLineColor: function () { return OURO; },
          fillColor: function () { return AVISO_FUNDO; },
          paddingLeft: function () { return 16; }, paddingRight: function () { return 16; },
          paddingTop: function () { return 12; }, paddingBottom: function () { return 12; } },
        table: { widths: ["*"], body: [[{ text: trechos(s.aviso), fontSize: 9.4,
                                           color: AVISO_TEXTO, lineHeight: 1.4 }]] } });
    }
    return saida;
  }

  // ----------------------------------------------------------------- blocos
  function cabecalho(titulo, conta) {
    return [
      { headlineLevel: 1, margin: [0, 14, 0, 0], columns: [
        { text: titulo, font: "Newsreader", fontSize: 15, color: TINTA, width: "auto" },
        conta ? { text: String(conta).toUpperCase(), fontSize: 7.2, bold: true, color: CINZA_CLARO,
                  characterSpacing: 1.2, margin: [12, 6, 0, 0], width: "*" } : { text: "" }
      ] },
      { canvas: [{ type: "line", x1: 0, y1: 4, x2: UTIL, y2: 4, lineWidth: 2, lineColor: OURO }],
        margin: [0, 0, 0, 6] }
    ];
  }

  function bloco(s) {
    var prods = s.produtos || [];
    var larguraProd = Math.min(56, Math.floor(300 / Math.max(1, prods.length)));
    var widths = ["*"], cab = [{ text: "PRESTADOR", style: "th" }];
    if (s.endereco) { widths.push(150); cab.push({ text: "ENDEREÇO", style: "th" }); }
    if (s.telefone) { widths.push(72); cab.push({ text: "TELEFONE", style: "th" }); }
    widths.push(92); cab.push({ text: "BAIRRO", style: "th" });
    prods.forEach(function (p) {
      widths.push(larguraProd);
      cab.push({ text: p.nome.toUpperCase(), style: "th", alignment: "center", fillColor: p.cor });
    });

    var corpo = [cab];
    s.linhas.forEach(function (l) {
      var nome = [{ text: l.nome, bold: true, fontSize: 8.6, color: TINTA }];
      if (l.tag) nome.push({ text: "  " + l.tag, bold: true, fontSize: 6.2, color: OURO_ESCURO,
                             characterSpacing: 0.6 });
      var pilha = [{ text: nome }];
      if (l.detalhe && l.detalhe.length) {
        pilha.push({ text: trechos(l.detalhe), fontSize: 7.3, color: CINZA, margin: [0, 1.5, 0, 0] });
      }
      if (l.esp) pilha.push({ text: l.esp, fontSize: 7.3, color: CINZA, margin: [0, 1.5, 0, 0] });
      var linha = [{ stack: pilha }];
      if (s.endereco) linha.push({ text: l.end || "—", fontSize: 7.6, color: TEXTO });
      if (s.telefone) linha.push({ text: l.tel || "—", fontSize: 7.6, color: TEXTO });
      linha.push({ stack: [
        { text: (l.bairro || "—").toUpperCase(), fontSize: 6.9, bold: true, color: CINZA },
        l.cidade ? { text: l.cidade, fontSize: 6.9, color: CINZA_CLARO } : ""
      ] });
      prods.forEach(function (p, i) {
        var m = l.marcas[i];
        if (!m) { linha.push({ text: "–", color: NAO, alignment: "center", fontSize: 9 }); return; }
        var cel = { stack: [{ svg: visto(p.cor), width: 10, alignment: "center" }] };
        if (m !== true) cel.stack.push({ text: m, fontSize: 6.2, bold: true, color: CINZA_CLARO,
                                         alignment: "center" });
        linha.push(cel);
      });
      corpo.push(linha);
    });

    return cabecalho(s.titulo, s.conta).concat([{
      table: { headerRows: 1, dontBreakRows: true, widths: widths, body: corpo },
      layout: {
        hLineWidth: function (i, no) { return i <= 1 ? 0 : 0.5; },
        hLineColor: function () { return RISCO; },
        vLineWidth: function () { return 0; },
        fillColor: function (r) { return r === 0 ? FUNDO : (r % 2 === 0 ? ZEBRA : null); },
        paddingLeft: function () { return 5; }, paddingRight: function () { return 5; },
        paddingTop: function (r) { return r === 0 ? 5 : 4; },
        paddingBottom: function (r) { return r === 0 ? 5 : 4; }
      }
    }]);
  }

  function texto(s) {
    var out = s.titulo ? cabecalho(s.titulo, s.conta) : [];
    out.push({ text: trechos(s.texto), fontSize: 10, color: TEXTO, lineHeight: 1.45,
               margin: [0, 2, 0, 6] });
    return out;
  }

  function grade(s) {
    var cor = s.cor || "#8b0633", linhas = [];
    for (var i = 0; i < s.itens.length; i += 2) {
      linhas.push([0, 1].map(function (k) {
        var it = s.itens[i + k];
        if (!it) return { text: "", fillColor: "#ffffff" };
        return { stack: [{ text: it.nome, bold: true, fontSize: 9, color: TINTA },
                         { text: it.sub || "", fontSize: 7.6, color: "#5a6a80", margin: [0, 2, 0, 0] }],
                 fillColor: "#faf7f8" };
      }));
    }
    return cabecalho(s.titulo, s.conta).concat([{
      table: { widths: ["*", "*"], dontBreakRows: true, body: linhas },
      layout: {
        hLineWidth: function () { return 5; }, hLineColor: function () { return "#ffffff"; },
        vLineWidth: function (i) { return i === 2 ? 0 : 2.4; },
        vLineColor: function () { return cor; },
        paddingLeft: function () { return 10; }, paddingRight: function () { return 10; },
        paddingTop: function () { return 6; }, paddingBottom: function () { return 6; }
      }
    }]);
  }

  // ------------------------------------------------------------- documento
  function montar(doc) {
    var conteudo = capa(doc.capa);
    doc.secoes.forEach(function (s) {
      if (s.tipo === "leitura") conteudo = conteudo.concat(leitura(s));
      else if (s.tipo === "bloco") { if (s.linhas.length) conteudo = conteudo.concat(bloco(s)); }
      else if (s.tipo === "texto") conteudo = conteudo.concat(texto(s));
      else if (s.tipo === "grade") { if (s.itens.length) conteudo = conteudo.concat(grade(s)); }
    });
    var cores = (doc.capa.cores && doc.capa.cores.length) ? doc.capa.cores : [OURO];
    return {
      pageSize: "A4", pageOrientation: "landscape", pageMargins: [M, M, M, 40],
      info: { title: doc.arquivo.replace(/\.pdf$/i, ""), author: doc.corretora || "Mazza Broker" },
      defaultStyle: { font: "Plex", fontSize: 8, lineHeight: 1.2 },
      styles: { th: { bold: true, fontSize: 6.6, color: "#ffffff", characterSpacing: 0.5 } },
      content: conteudo,
      background: function (pagina) {
        if (pagina !== 1) return null;
        var lf = L / cores.length, c = [{ type: "rect", x: 0, y: 0, w: L, h: A, color: FUNDO }];
        cores.forEach(function (cor, i) {
          c.push({ type: "rect", x: i * lf, y: A - 17, w: lf + 0.5, h: 17, color: cor });
        });
        return { canvas: c };
      },
      footer: function (pagina, total) {
        if (pagina === 1) return null;
        return { margin: [M, 10, M, 0], stack: [
          linhaCanvas(UTIL, OURO, 0.7),
          { margin: [0, 5, 0, 0], columns: [
            { width: "*", fontSize: 6.8, color: CINZA_CLARO, bold: true, text: [
              { text: (doc.corretora || "Mazza Broker").toUpperCase(), color: OURO_ESCURO,
                characterSpacing: 1.6 },
              { text: (doc.corretorNome ? "   ·   " + doc.corretorNome : "") +
                      "   ·   " + (doc.rodape || "") }
            ] },
            { width: "auto", fontSize: 6.8, bold: true, color: CINZA_CLARO, alignment: "right", text: [
              "Confirme no portal da operadora antes de contratar      ",
              { text: pagina + " / " + total, color: OURO_ESCURO, fontSize: 8 }
            ] }
          ] }
        ] };
      },
      pageBreakBefore: function (no) {
        // titulo de bloco no pe da pagina vai junto com a tabela para a proxima
        return no.headlineLevel === 1 && no.startPosition.top > A - 40 - 90;
      }
    };
  }

  global.PDFRede = {
    montar: montar,
    baixar: function (doc) {
      return carregar().then(function (pm) {
        var pdf = pm.createPdf(montar(doc));
        // nome de arquivo sem acento: com "Saúde" o Chrome desiste do nome
        // e salva como "download"
        var nome = doc.arquivo.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
        var r = pdf.download(nome);
        return r && r.then ? r : undefined;
      });
    }
  };
})(window);
