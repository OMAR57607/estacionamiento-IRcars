/*
 * Mejora progresiva de la caja.
 *
 * Reglas de este archivo, y por qué:
 *
 * - **Nada depende de él.** Toda operación se completa con JavaScript
 *   desactivado: los formularios envían lo mismo y el servidor decide. Aquí
 *   solo se ahorran toques y se adelanta una cifra.
 * - **El servidor es la única autoridad sobre los importes.** El cambio que
 *   se calcula aquí se rotula como estimado; el oficial es el que devuelve el
 *   cobro.
 * - **Dinero en centavos enteros.** `0.1 + 0.2` no es `0.3` en coma flotante,
 *   y una caja que dice «Cambio: $55.32999» pierde la confianza del cliente.
 * - **Sin sintaxis reciente** (ni flechas, ni `let`, ni módulos): la caseta
 *   puede tener un teléfono de hace años.
 * - **Sin estado propio.** Cada comportamiento se activa por un atributo
 *   `data-` en el marcado; si el atributo no está, no ocurre nada.
 */
(function () {
  "use strict";

  /* Placa normalizada como en el servidor: mayúsculas, sin espacios ni
     guiones. Así «abc-12» encuentra «ABC1234». */
  function normalizar(texto) {
    return (texto || "").toUpperCase().replace(/[\s-]+/g, "");
  }

  /* "100.5" → 10050. Devuelve null si no es una cantidad. */
  function aCentavos(texto) {
    var limpio = String(texto || "").replace(/[$,\s]/g, "");
    if (!/^\d+(\.\d{0,2})?$/.test(limpio)) { return null; }
    var partes = limpio.split(".");
    var decimales = (partes[1] || "") + "00";
    return parseInt(partes[0], 10) * 100 + parseInt(decimales.slice(0, 2), 10);
  }

  function aPesos(centavos) {
    var entero = Math.floor(centavos / 100);
    var resto = String(centavos % 100);
    if (resto.length < 2) { resto = "0" + resto; }
    return "$" + entero.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",") + "." + resto;
  }

  /* --- data-mayusculas: la placa se ve como se va a guardar ------------- */
  function mayusculas(campo) {
    campo.addEventListener("input", function () {
      var inicio = campo.selectionStart;
      var fin = campo.selectionEnd;
      var alto = campo.value.toUpperCase();
      if (alto !== campo.value) {
        campo.value = alto;
        try { campo.setSelectionRange(inicio, fin); } catch (e) { /* tipo sin selección */ }
      }
    });
  }

  /* --- data-filtra="#patio": oculta las filas que no contienen lo tecleado */
  function filtra(campo) {
    var lista = document.querySelector(campo.getAttribute("data-filtra"));
    if (!lista) { return; }
    var filas = lista.querySelectorAll("[data-placa]");
    var contador = document.querySelector(campo.getAttribute("data-contador") || "#contador-patio");
    var total = filas.length;

    function aplicar() {
      var buscado = normalizar(campo.value);
      var visibles = 0;
      for (var i = 0; i < filas.length; i++) {
        var coincide = !buscado || normalizar(filas[i].getAttribute("data-placa")).indexOf(buscado) !== -1;
        filas[i].hidden = !coincide;
        if (coincide) { visibles++; }
      }
      if (contador) {
        contador.textContent = buscado ? visibles + " de " + total : total + " en total";
      }
    }
    campo.addEventListener("input", aplicar);
    aplicar();
  }

  /* --- data-cambio: cambio estimado mientras se teclea el pago ---------- */
  function cambio(campo) {
    var importe = aCentavos(campo.getAttribute("data-importe"));
    var salida = document.querySelector(campo.getAttribute("data-cambio"));
    if (importe === null || !salida) { return; }

    function aplicar() {
      var pago = aCentavos(campo.value);
      salida.classList.remove("falta", "sobra");
      if (pago === null || campo.value === "") {
        salida.textContent = "";
        return;
      }
      if (pago < importe) {
        salida.textContent = "Faltan " + aPesos(importe - pago);
        salida.classList.add("falta");
      } else if (pago === importe) {
        salida.textContent = "Pago exacto, sin cambio";
        salida.classList.add("sobra");
      } else {
        salida.textContent = "Cambio estimado: " + aPesos(pago - importe);
        salida.classList.add("sobra");
      }
    }
    campo.addEventListener("input", aplicar);
    aplicar();
  }

  /* --- data-monto: los atajos de billete escriben el pago --------------- */
  function monto(boton) {
    boton.addEventListener("click", function () {
      var destino = document.querySelector(boton.getAttribute("data-destino") || "#id_pago_recibido");
      if (!destino) { return; }
      destino.value = boton.getAttribute("data-monto");
      // El evento sintético recalcula el cambio estimado como si se hubiera
      // tecleado.
      var evento = document.createEvent("Event");
      evento.initEvent("input", true, true);
      destino.dispatchEvent(evento);
      destino.focus();
    });
  }

  /* --- data-confirma: un paso más en lo que no se deshace --------------- */
  function confirma(formulario) {
    formulario.addEventListener("submit", function (evento) {
      if (!window.confirm(formulario.getAttribute("data-confirma"))) {
        evento.preventDefault();
      }
    });
  }

  function cada(selector, fn) {
    var nodos = document.querySelectorAll(selector);
    for (var i = 0; i < nodos.length; i++) { fn(nodos[i]); }
  }

  document.addEventListener("DOMContentLoaded", function () {
    // Los botones de atajo solo tienen sentido con este archivo cargado: el
    // marcado los trae ocultos y aquí se muestran.
    cada("[data-atajos]", function (n) { n.hidden = false; });
    cada("[data-mayusculas]", mayusculas);
    cada("[data-filtra]", filtra);
    cada("[data-cambio]", cambio);
    cada("[data-monto]", monto);
    cada("form[data-confirma]", confirma);
  });
}());
