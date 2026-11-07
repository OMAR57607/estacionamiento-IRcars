"""
Modelos de persistencia (capa de infraestructura).

Criterios de diseño aplicados:

* **Integridad en la base, no solo en el código.** Las reglas "un cajón no
  puede tener dos folios abiertos" y "una placa no puede entrar dos veces sin
  salir" se expresan como restricciones únicas parciales de PostgreSQL. Si dos
  cajas registran una entrada en el mismo milisegundo, la base rechaza la
  segunda. Validar únicamente en Python dejaría una condición de carrera.

* **Importes reproducibles.** Al cobrar, los parámetros de la tarifa se copian
  al folio. Si mañana sube la cuota, el folio de ayer sigue explicando su
  propio importe. Sin esta copia, cualquier cambio de precio reescribiría la
  historia contable.

* **Borrado protegido.** Tarifas y cajones referidos por folios no pueden
  eliminarse (`PROTECT`): la trazabilidad de un movimiento con efecto
  económico no debe romperse.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from .domain.tarifas import ParametrosTarifa


class TipoCajon(models.TextChoices):
    """Clasificación del espacio. Condiciona la asignación en la entrada."""

    AUTOMOVIL = "AUTO", "Automóvil"
    MOTOCICLETA = "MOTO", "Motocicleta"
    ACCESIBLE = "ACCESIBLE", "Accesible (personas con discapacidad)"


class TipoVehiculo(models.TextChoices):
    """
    Lo que determina **qué precio se cobra**.

    Se declara aparte de `TipoCajon` aunque los valores se parezcan, porque
    responden a preguntas distintas: el tipo de cajón dice dónde cabe el
    vehículo —y por eso incluye «accesible», que es un espacio reservado, no
    una clase de vehículo— y el tipo de vehículo dice cuánto paga. Un auto
    estacionado en un cajón accesible paga tarifa de auto.

    Mezclarlos obligaría a decidir qué cobra un «cajón accesible», que es una
    pregunta sin respuesta.
    """

    AUTOMOVIL = "AUTO", "Automóvil"
    MOTOCICLETA = "MOTO", "Motocicleta"

    @classmethod
    def desde_cajon(cls, tipo_cajon: str) -> str:
        """Qué precio corresponde a quien pide un cajón de este tipo."""
        return (
            cls.MOTOCICLETA
            if tipo_cajon == TipoCajon.MOTOCICLETA
            else cls.AUTOMOVIL
        )


class EstadoFolio(models.TextChoices):
    ABIERTO = "ABIERTO", "Abierto"
    CERRADO = "CERRADO", "Cerrado y liquidado"
    CANCELADO = "CANCELADO", "Cancelado"


class EstadoTurno(models.TextChoices):
    ABIERTO = "ABIERTO", "Abierto"
    CERRADO = "CERRADO", "Cerrado"


class Turno(models.Model):
    """
    Turno de caja: la unidad del corte.

    Cada cobro queda adscrito al turno en que ocurrió, de modo que el corte
    sea reconstruible y atribuible a un operador identificado.

    Los campos del corte (`total_cobrado`, `efectivo_esperado`, `diferencia`)
    se graban como fotografía en el momento del cierre. Son redundantes
    respecto a los folios a propósito: un corte firmado no debe recalcularse
    después, porque un recálculo con datos posteriores cambiaría una cifra ya
    declarada por el operador.
    """

    operador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="turnos",
        verbose_name="operador",
    )
    estado = models.CharField(
        "estado", max_length=10, choices=EstadoTurno.choices, default=EstadoTurno.ABIERTO
    )

    fondo_inicial = models.DecimalField(
        "fondo inicial",
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Efectivo con el que inicia la caja (cambio).",
    )
    abierto_en = models.DateTimeField("abierto en", default=timezone.now)
    cerrado_en = models.DateTimeField("cerrado en", null=True, blank=True)

    # --- Fotografía del corte (se graba al cerrar) -------------------------
    efectivo_declarado = models.DecimalField(
        "efectivo declarado",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Efectivo contado físicamente por el operador (cierre ciego).",
    )
    total_cobrado = models.DecimalField(
        "total cobrado", max_digits=12, decimal_places=2, null=True, blank=True
    )
    efectivo_esperado = models.DecimalField(
        "efectivo esperado", max_digits=12, decimal_places=2, null=True, blank=True
    )
    diferencia = models.DecimalField(
        "diferencia",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Positiva = sobrante; negativa = faltante.",
    )
    folios_cobrados = models.PositiveIntegerField(
        "folios cobrados", null=True, blank=True
    )
    folios_cortesia = models.PositiveIntegerField(
        "folios en cortesía", null=True, blank=True
    )
    resumen = models.JSONField(
        "resumen del corte",
        null=True,
        blank=True,
        help_text="Detalle emitido por el motor de corte. Base del comprobante.",
    )
    observaciones = models.TextField("observaciones", blank=True)

    # --- Corrección del corte (ver AjusteCorte) ----------------------------
    #
    # El corte cerrado es inmutable y los campos de arriba no se reescriben
    # nunca. Cuando la cifra declarada estaba mal —el caso que motivó esto:
    # un turno cerrado con $140 menos de los que había en caja—, la
    # corrección se registra como asiento aparte y su efecto acumulado se
    # refleja aquí.
    #
    # Son redundantes respecto a la bitácora de ajustes, igual que los
    # campos del corte son redundantes respecto a los folios, y por el mismo
    # motivo: el reporte de un mes consulta miles de turnos y recorrer sus
    # ajustes uno a uno para saber cuánto falta en caja convertiría una
    # consulta agregada en un recorrido en memoria. `NULL` significa «nunca
    # se ajustó», que no es lo mismo que «se ajustó a cero».
    efectivo_ajustado = models.DecimalField(
        "efectivo corregido",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Efectivo real tras la corrección administrativa. Vacío si el "
            "corte nunca se ajustó."
        ),
    )
    diferencia_ajustada = models.DecimalField(
        "diferencia corregida",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Diferencia resultante del último ajuste aplicado.",
    )

    class Meta:
        verbose_name = "turno"
        verbose_name_plural = "turnos"
        ordering = ["-abierto_en"]
        constraints = [
            # Un operador no puede tener dos turnos abiertos: el efectivo
            # quedaría repartido entre dos cortes sin forma de reconciliarlo.
            models.UniqueConstraint(
                fields=["operador"],
                condition=models.Q(estado=EstadoTurno.ABIERTO),
                name="uq_operador_un_turno_abierto",
            ),
            # Un turno cerrado debe tener siempre fecha de cierre y conteo.
            models.CheckConstraint(
                condition=models.Q(estado=EstadoTurno.ABIERTO)
                | models.Q(cerrado_en__isnull=False, efectivo_declarado__isnull=False),
                name="ck_turno_cerrado_completo",
            ),
        ]
        indexes = [
            models.Index(fields=["estado", "abierto_en"], name="idx_turno_estado_fecha"),
        ]

    def __str__(self) -> str:
        return f"Turno {self.pk} · {self.operador} · {self.abierto_en:%Y-%m-%d %H:%M}"

    # Las tres propiedades siguientes existen para que ninguna pantalla,
    # reporte ni PDF tenga que recordar la regla «si hay ajuste usa el
    # ajustado». Escrita una vez, un olvido deja de poder mostrar la cifra
    # equivocada en un documento que alguien firma.

    @property
    def tiene_ajuste(self) -> bool:
        """Si el corte fue corregido después de cerrarse."""
        return self.efectivo_ajustado is not None

    @property
    def efectivo_vigente(self) -> Decimal | None:
        """Efectivo que el corte da por bueno hoy."""
        if self.efectivo_ajustado is not None:
            return self.efectivo_ajustado
        return self.efectivo_declarado

    @property
    def diferencia_vigente(self) -> Decimal | None:
        """
        Diferencia que el corte da por buena hoy.

        Es la cifra que debe leerse para saber si la caja cuadra. La original
        sigue disponible en `diferencia` y el comprobante muestra las dos:
        una sin la otra cuenta media historia.
        """
        if self.efectivo_ajustado is not None:
            return self.diferencia_ajustada
        return self.diferencia


class Tarifa(models.Model):
    """
    Cuadro tarifario vigente.

    Se conserva el histórico completo: las tarifas no se editan ni se borran,
    se desactivan y se crea una nueva. Esto permite responder con precisión
    qué precio estaba exhibido en una fecha determinada.
    """

    nombre = models.CharField("nombre", max_length=100, unique=True)
    tipo_vehiculo = models.CharField(
        "tipo de vehículo",
        max_length=10,
        choices=TipoVehiculo.choices,
        default=TipoVehiculo.AUTOMOVIL,
        help_text="Qué vehículos pagan este precio.",
    )
    cuota_hora = models.DecimalField(
        "cuota por hora",
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Precio de una hora completa de permanencia.",
    )
    minutos_cortesia = models.PositiveIntegerField(
        "minutos de cortesía",
        default=0,
        help_text="Tolerancia sin costo. Ej. 15 minutos para maniobras.",
    )
    fraccion_minutos = models.PositiveSmallIntegerField(
        "fracción de cobro (minutos)",
        default=1,
        help_text=(
            "Bloque mínimo facturable. 1 = cobro por minuto real "
            "(proporcionalidad estricta). Un valor mayor redondea hacia arriba."
        ),
    )
    minutos_tolerancia = models.PositiveSmallIntegerField(
        "tolerancia por hora (minutos)",
        default=0,
        help_text=(
            "Minutos de gracia sobre cada hora cumplida. Dentro de ellos se "
            "cobra el tiempo real; al rebasarlos se cobra la hora completa. "
            "Ej. 15: a la 1:10 se cobra proporcional y a la 1:16, dos horas. "
            "0 = sin salto, cobro proporcional siempre."
        ),
    )
    cobro_minimo = models.DecimalField(
        "cobro mínimo",
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    tope_diario = models.DecimalField(
        "tope diario",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Importe máximo por día natural iniciado. Vacío = sin tope.",
    )
    activa = models.BooleanField("activa", default=True)
    vigente_desde = models.DateTimeField("vigente desde", default=timezone.now)
    creada_en = models.DateTimeField("creada en", auto_now_add=True)

    class Meta:
        verbose_name = "tarifa"
        verbose_name_plural = "tarifas"
        ordering = ["-vigente_desde"]
        constraints = [
            # Dos precios activos para el mismo tipo de vehículo dejarían el
            # cobro a merced del orden de una consulta. La restricción lo hace
            # imposible en la base, no solo en el código que la escribe.
            models.UniqueConstraint(
                fields=["tipo_vehiculo"],
                condition=models.Q(activa=True),
                name="uq_tarifa_activa_por_tipo",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.nombre} · {self.get_tipo_vehiculo_display()} (${self.cuota_hora}/h)"

    def a_parametros(self) -> ParametrosTarifa:
        """
        Traduce el registro al objeto de dominio.

        Esta es la única frontera entre la persistencia y el motor de cálculo:
        el motor jamás recibe una instancia de modelo.
        """
        return ParametrosTarifa(
            cuota_hora=self.cuota_hora,
            minutos_cortesia=self.minutos_cortesia,
            fraccion_minutos=self.fraccion_minutos,
            minutos_tolerancia=self.minutos_tolerancia,
            cobro_minimo=self.cobro_minimo,
            tope_diario=self.tope_diario,
        )


class Cajon(models.Model):
    """Espacio físico de estacionamiento."""

    codigo = models.CharField(
        "código", max_length=20, unique=True, help_text="Ej. A-01, B-14."
    )
    zona = models.CharField("zona", max_length=50, blank=True)
    tipo = models.CharField(
        "tipo", max_length=12, choices=TipoCajon.choices, default=TipoCajon.AUTOMOVIL
    )
    activo = models.BooleanField(
        "activo",
        default=True,
        help_text="Desactive el cajón si está fuera de servicio (obra, reservado).",
    )

    class Meta:
        verbose_name = "cajón"
        verbose_name_plural = "cajones"
        ordering = ["codigo"]
        indexes = [
            # La consulta de asignación filtra por tipo y disponibilidad.
            models.Index(fields=["tipo", "activo"], name="idx_cajon_tipo_activo"),
        ]

    def __str__(self) -> str:
        return f"{self.codigo} [{self.get_tipo_display()}]"

    def save(self, *args, **kwargs):
        # Normalización en mayúsculas: el código es un identificador operativo
        # y debe ser insensible a cómo lo capture el operador.
        self.codigo = self.codigo.strip().upper()
        super().save(*args, **kwargs)


class Folio(models.Model):
    """
    Boleto de estacionamiento: un ciclo completo de entrada, cobro y salida.

    Es el registro contable de la operación. Una vez cerrado, no debe
    modificarse: cualquier corrección se documenta con una cancelación y un
    nuevo folio.
    """

    uuid = models.UUIDField(
        "folio", default=uuid.uuid4, editable=False, unique=True, db_index=True
    )
    placa = models.CharField("placa", max_length=15, db_index=True)
    # Opcional a propósito: un estacionamiento que solo controla tiempo y caja
    # no necesita inventariar cajones. Si se define inventario, la asignación
    # se vuelve obligatoria y el control de capacidad entra en vigor; si no
    # hay inventario, el folio se emite sin cajón y la operación no se detiene.
    cajon = models.ForeignKey(
        Cajon,
        on_delete=models.PROTECT,
        related_name="folios",
        verbose_name="cajón",
        null=True,
        blank=True,
    )
    turno = models.ForeignKey(
        "Turno",
        on_delete=models.PROTECT,
        related_name="folios",
        verbose_name="turno de cobro",
        null=True,
        blank=True,
        help_text="Turno en que se cobró el folio. Base del corte de caja.",
    )
    tarifa = models.ForeignKey(
        Tarifa, on_delete=models.PROTECT, related_name="folios", verbose_name="tarifa"
    )
    estado = models.CharField(
        "estado", max_length=10, choices=EstadoFolio.choices, default=EstadoFolio.ABIERTO
    )

    entrada_en = models.DateTimeField("entrada", default=timezone.now)
    salida_en = models.DateTimeField("salida", null=True, blank=True)
    minutos = models.PositiveIntegerField("minutos de permanencia", null=True, blank=True)

    importe = models.DecimalField(
        "importe cobrado", max_digits=10, decimal_places=2, null=True, blank=True
    )
    pago_recibido = models.DecimalField(
        "pago recibido", max_digits=10, decimal_places=2, null=True, blank=True
    )
    cambio = models.DecimalField(
        "cambio entregado", max_digits=10, decimal_places=2, null=True, blank=True
    )

    # --- Copia de la tarifa en el momento del cobro (auditabilidad) ---------
    # Redundante por diseño: garantiza que el importe sea reproducible aunque
    # el cuadro tarifario cambie después del cierre.
    tarifa_cuota_hora = models.DecimalField(
        "cuota/hora aplicada", max_digits=10, decimal_places=2, null=True, blank=True
    )
    tarifa_minutos_cortesia = models.PositiveIntegerField(
        "cortesía aplicada (min)", null=True, blank=True
    )
    tarifa_fraccion_minutos = models.PositiveSmallIntegerField(
        "fracción aplicada (min)", null=True, blank=True
    )
    tarifa_minutos_tolerancia = models.PositiveSmallIntegerField(
        "tolerancia aplicada (min)", null=True, blank=True
    )
    tarifa_cobro_minimo = models.DecimalField(
        "cobro mínimo aplicado", max_digits=10, decimal_places=2, null=True, blank=True
    )
    tarifa_tope_diario = models.DecimalField(
        "tope diario aplicado", max_digits=10, decimal_places=2, null=True, blank=True
    )
    desglose = models.JSONField(
        "desglose del cálculo",
        null=True,
        blank=True,
        help_text="Detalle emitido por el motor de tarifas. Base del comprobante.",
    )

    # --- Trazabilidad -------------------------------------------------------
    operador_entrada = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="folios_entrada",
        verbose_name="operador de entrada",
    )
    operador_salida = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="folios_salida",
        verbose_name="operador de salida",
    )
    observaciones = models.TextField("observaciones", blank=True)
    creado_en = models.DateTimeField("creado en", auto_now_add=True)
    actualizado_en = models.DateTimeField("actualizado en", auto_now=True)

    class Meta:
        verbose_name = "folio"
        verbose_name_plural = "folios"
        ordering = ["-entrada_en"]
        constraints = [
            # Doble ocupación imposible a nivel de base de datos.
            models.UniqueConstraint(
                fields=["cajon"],
                condition=models.Q(estado=EstadoFolio.ABIERTO),
                name="uq_cajon_un_folio_abierto",
            ),
            # Una placa no puede tener dos folios abiertos simultáneamente.
            models.UniqueConstraint(
                fields=["placa"],
                condition=models.Q(estado=EstadoFolio.ABIERTO),
                name="uq_placa_un_folio_abierto",
            ),
            # Un folio cerrado debe tener siempre salida e importe.
            models.CheckConstraint(
                condition=models.Q(estado=EstadoFolio.ABIERTO)
                | models.Q(estado=EstadoFolio.CANCELADO)
                | models.Q(salida_en__isnull=False, importe__isnull=False),
                name="ck_folio_cerrado_completo",
            ),
        ]
        indexes = [
            models.Index(fields=["estado", "entrada_en"], name="idx_folio_estado_fecha"),
        ]

    def __str__(self) -> str:
        return f"Folio {self.uuid} · {self.placa}"

    def save(self, *args, **kwargs):
        # La placa se almacena normalizada en mayúsculas y sin espacios. Esto
        # habilita búsquedas insensibles a mayúsculas sin penalizar índices y
        # evita duplicados del tipo "abc123" / "ABC 123".
        self.placa = self.placa.strip().upper().replace(" ", "")
        super().save(*args, **kwargs)


class MotivoAjuste(models.TextChoices):
    """
    Por qué se corrige un corte ya firmado.

    La lista es cerrada a propósito. Un campo de texto libre produce bitácoras
    en las que cada ajuste se explica con otras palabras y no hay forma de
    contar cuántas veces pasó lo mismo —que es justo lo que delata un problema
    recurrente—. La justificación escrita sigue siendo obligatoria y va además
    del motivo, no en su lugar.
    """

    RECUENTO = "RECUENTO", "Error de conteo: se recontó el efectivo"
    LOCALIZADO = "LOCALIZADO", "Efectivo localizado después del cierre"
    NO_REGISTRADO = "NO_REGISTRADO", "Cobro no registrado en el sistema"
    CAPTURA = "CAPTURA", "Error al teclear el efectivo declarado"
    RETIRO = "RETIRO", "Retiro de efectivo no descontado"
    OTRO = "OTRO", "Otro (se detalla en la justificación)"


class AjusteCorte(models.Model):
    """
    Asiento de corrección sobre un corte ya cerrado.

    Responde a una necesidad real de operación: un turno se cerró declarando
    $140 menos de los que había en caja, y el sistema no ofrecía forma de
    corregirlo. Las dos salidas que quedaban eran malas. Dejar el faltante
    falso envenena el histórico —si la diferencia de un corte puede ser un
    error de dedo, deja de significar nada y el control de caja entero se
    vuelve decorativo—. Editar la base a mano corrige la cifra y borra al
    responsable.

    **El corte original no se reescribe.** `Turno` conserva intactos el
    efectivo que el operador declaró y la diferencia que resultó. Este
    registro guarda aparte la cifra corregida, quién la autorizó, cuándo y
    por qué. El comprobante muestra las dos, y es esa coexistencia la que
    hace al ajuste un acto auditable en lugar de un borrado: un faltante que
    puede desaparecer sin rastro no disuade a nadie de provocarlo.

    Varios ajustes sobre un mismo turno son válidos y se acumulan. Cada uno
    registra de qué cifra partió, de modo que la cadena completa se reconstruye
    sin ambigüedad aunque alguien corrija dos veces.
    """

    turno = models.ForeignKey(
        Turno,
        on_delete=models.PROTECT,
        related_name="ajustes",
        verbose_name="turno corregido",
    )

    # --- Cifras del asiento -------------------------------------------------
    efectivo_esperado = models.DecimalField(
        "efectivo esperado",
        max_digits=12,
        decimal_places=2,
        help_text=(
            "Copia de lo que el sistema esperaba. Redundante a propósito: el "
            "asiento debe explicar su propio monto sin depender del turno."
        ),
    )
    efectivo_anterior = models.DecimalField(
        "efectivo declarado antes del ajuste", max_digits=12, decimal_places=2
    )
    efectivo_nuevo = models.DecimalField(
        "efectivo real", max_digits=12, decimal_places=2
    )
    monto = models.DecimalField(
        "monto del ajuste",
        max_digits=12,
        decimal_places=2,
        help_text="Positivo = apareció efectivo; negativo = había menos.",
    )
    diferencia_anterior = models.DecimalField(
        "diferencia antes del ajuste", max_digits=12, decimal_places=2
    )
    diferencia_nueva = models.DecimalField(
        "diferencia después del ajuste", max_digits=12, decimal_places=2
    )

    # --- Responsabilidad ----------------------------------------------------
    motivo = models.CharField(
        "motivo", max_length=20, choices=MotivoAjuste.choices
    )
    justificacion = models.TextField(
        "justificación",
        help_text=(
            "Qué pasó, con detalle suficiente para que se entienda meses "
            "después sin preguntar a nadie."
        ),
    )
    autorizo = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ajustes_autorizados",
        verbose_name="autorizó",
        help_text="Perfil administrativo que respondió por la corrección.",
    )
    creado_en = models.DateTimeField("registrado en", default=timezone.now)

    class Meta:
        verbose_name = "ajuste de corte"
        verbose_name_plural = "ajustes de corte"
        ordering = ["-creado_en"]
        constraints = [
            # Un ajuste de cero no corrige nada y solo ensucia la bitácora.
            # Se impide en la base y no solo en el servicio: la restricción
            # sobrevive a cualquier vía de entrada futura.
            models.CheckConstraint(
                condition=~models.Q(monto=Decimal("0.00")),
                name="ck_ajuste_monto_no_cero",
            ),
            # Un ajuste sin justificación escrita es indistinguible de un
            # faltante encubierto.
            models.CheckConstraint(
                condition=~models.Q(justificacion=""),
                name="ck_ajuste_justificado",
            ),
        ]
        indexes = [
            models.Index(fields=["turno", "creado_en"], name="idx_ajuste_turno_fecha"),
        ]

    def __str__(self) -> str:
        return f"Ajuste de {self.monto:+.2f} al turno {self.turno_id}"
