"""
Persistencia de la publicación de boletos.

Aquí conviven **dos modelos que nunca se usan en el mismo lugar**, y conviene
tenerlo presente al leer el archivo:

* `PublicacionPendiente` vive en la **caja** (SQLite del ejecutable). Es la
  bandeja de salida: lo que falta por enviar a la nube.
* `BoletoPublicado` vive en la **nube** (PostgreSQL del servicio de boletos).
  Es la copia de solo lectura que consulta el cliente con su teléfono.

Están en la misma aplicación porque son las dos mitades de un mismo contrato
—lo que se encola es exactamente lo que se recibe— y mantenerlas juntas evita
que una evolucione sin la otra. El costo es una tabla sin uso en cada extremo,
de unas pocas decenas de bytes: a cambio, el contrato no puede desincronizarse
sin que el cambio se vea en un solo archivo.

Principio que gobierna todo este módulo: **la nube no es la fuente de verdad.**
La caja lo es. Si el servicio de boletos se borra por completo, la operación no
pierde un solo peso ni un solo folio; se repuebla publicando de nuevo. Por eso
aquí no hay importes de caja, ni turnos, ni operadores, ni placas completas.
"""
from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class EstadoBoleto(models.TextChoices):
    """
    Espejo deliberadamente desacoplado de `EstadoFolio`.

    Se declara aquí en lugar de importar el del estacionamiento porque el
    servicio en la nube no necesita —ni debe— conocer el modelo de folios. La
    validación de la ingesta se encarga de que solo lleguen valores de esta
    lista.
    """

    ABIERTO = "ABIERTO", "En el estacionamiento"
    CERRADO = "CERRADO", "Liquidado"
    CANCELADO = "CANCELADO", "Cancelado"


class PublicacionPendiente(models.Model):
    """
    Bandeja de salida transaccional (*transactional outbox*).

    El problema que resuelve: registrar la entrada de un vehículo y avisar a un
    servicio remoto son dos operaciones con modos de falla distintos. Si la
    caja llamara a la nube en línea, una red lenta retrasaría la fila de
    clientes y una red caída impediría cobrar. Inaceptable: la caja debe operar
    igual con o sin internet.

    La solución es escribir la intención de publicar **en la misma transacción**
    que el folio. Si la transacción se confirma, el aviso está garantizado; si
    se revierte, no queda un aviso huérfano de un folio que no existe. El envío
    real ocurre después, fuera del camino crítico, y puede reintentarse tantas
    veces como haga falta.

    `uuid_folio` es único: la fila es un *upsert*, no un registro histórico. Si
    un folio cambia tres veces antes de lograr conexión, se publica una sola
    vez con su último estado. Esto acota el tamaño de la bandeja al número de
    folios con cambios sin enviar, no al número de cambios.
    """

    uuid_folio = models.UUIDField("folio", unique=True, db_index=True)
    cuerpo = models.JSONField(
        "instantánea pública",
        help_text=(
            "Carga exacta que se enviará al servicio de boletos, ya enmascarada. "
            "Se graba resuelta y no se recalcula al enviar: así lo publicado "
            "corresponde al estado del folio en el momento del cambio."
        ),
    )

    intentos = models.PositiveIntegerField("intentos de envío", default=0)
    proximo_intento_en = models.DateTimeField(
        "próximo intento", default=timezone.now, db_index=True
    )
    ultimo_error = models.TextField(
        "último error",
        blank=True,
        help_text=(
            "Diagnóstico del último envío fallido. Nunca debe contener el token "
            "de publicación."
        ),
    )

    creada_en = models.DateTimeField("creada en", auto_now_add=True)
    actualizada_en = models.DateTimeField("actualizada en", auto_now=True)

    class Meta:
        verbose_name = "publicación pendiente"
        verbose_name_plural = "publicaciones pendientes"
        # Más antiguo primero: el cliente que lleva más tiempo esperando su
        # boleto es el que debe publicarse antes.
        ordering = ["proximo_intento_en", "creada_en"]
        indexes = [
            models.Index(
                fields=["proximo_intento_en", "intentos"],
                name="idx_pendiente_turno_envio",
            ),
        ]

    def __str__(self) -> str:
        return f"Pendiente {self.uuid_folio} (intentos: {self.intentos})"


class BoletoPublicado(models.Model):
    """
    Copia pública y de solo lectura de un folio, servida al cliente por QR.

    Qué contiene y por qué exactamente eso:

    * **La placa enmascarada, nunca la completa.** El enmascarado se aplica en
      la caja, *antes* de enviar. La consecuencia es la que importa: la placa
      íntegra nunca viaja por la red ni llega a existir en la nube, de modo que
      un acceso indebido a la base del servicio de boletos no puede revelarla.
      Minimizar en el origen es la única forma de minimización que resiste el
      compromiso del destino.

    * **Los parámetros tarifarios exhibidos al entrar**, no el importe. El
      importe acumulado se calcula al vuelo con el mismo motor puro que usa la
      caja (`apps.estacionamiento.domain.tarifas.cotizar`), por lo que el
      boleto muestra siempre una cifra coherente con la que cobrará el
      operador. Guardar un importe congelado obligaría a republicar cada
      minuto; guardar los parámetros lo hace innecesario.

    * **Nada de la caja.** Sin operador, sin turno, sin fondo, sin corte, sin
      pago recibido ni cambio entregado. El servicio público no tiene por qué
      poder responder cuánto dinero hay en la caseta.

    La clave de acceso es `uuid_folio`, un UUID versión 4 generado por la caja
    (`Folio.uuid`). No se expone ni se replica el `id` autoincremental: una
    dirección con un entero consecutivo sería enumerable y permitiría recorrer
    los boletos del día entero a partir de uno solo.
    """

    uuid_folio = models.UUIDField(
        "folio",
        unique=True,
        db_index=True,
        help_text="Identificador no adivinable. Es la llave de la dirección pública.",
    )
    placa_enmascarada = models.CharField(
        "placa (enmascarada)",
        max_length=30,
        help_text="Nunca la placa completa. Ver `dominio.enmascarar_placa`.",
    )
    estado = models.CharField(
        "estado", max_length=10, choices=EstadoBoleto.choices, default=EstadoBoleto.ABIERTO
    )
    ubicacion = models.CharField(
        "cajón",
        max_length=20,
        blank=True,
        help_text="Código del cajón asignado, si el predio inventaría cajones.",
    )

    entrada_en = models.DateTimeField("entrada")
    salida_en = models.DateTimeField("salida", null=True, blank=True)
    minutos = models.PositiveIntegerField("minutos liquidados", null=True, blank=True)
    importe = models.DecimalField(
        "importe liquidado", max_digits=10, decimal_places=2, null=True, blank=True
    )

    # --- Parámetros de la tarifa exhibida al ingresar -----------------------
    # Son copia, no referencia: el servicio de boletos no tiene catálogo de
    # tarifas y no debe tenerlo. Reproducen el precio que el cliente vio al
    # entrar, que es el que la normativa de PROFECO obliga a respetar al
    # cobrar.
    cuota_hora = models.DecimalField(
        "cuota por hora",
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    minutos_cortesia = models.PositiveIntegerField("minutos de cortesía", default=0)
    fraccion_minutos = models.PositiveSmallIntegerField(
        "fracción de cobro (minutos)", default=1
    )
    minutos_tolerancia = models.PositiveSmallIntegerField(
        "tolerancia por hora (minutos)", default=0
    )
    cobro_minimo = models.DecimalField(
        "cobro mínimo", max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    tope_diario = models.DecimalField(
        "tope diario", max_digits=10, decimal_places=2, null=True, blank=True
    )

    recibido_en = models.DateTimeField("recibido en", auto_now_add=True)
    actualizado_en = models.DateTimeField("actualizado en", auto_now=True)

    class Meta:
        verbose_name = "boleto publicado"
        verbose_name_plural = "boletos publicados"
        ordering = ["-entrada_en"]
        indexes = [
            # La depuración periódica consulta por estado y fecha de salida.
            models.Index(fields=["estado", "salida_en"], name="idx_boleto_estado_salida"),
        ]

    def __str__(self) -> str:
        return f"Boleto {self.uuid_folio} · {self.placa_enmascarada}"

    # --- Consultas de presentación -----------------------------------------

    def a_parametros(self):
        """
        Traduce los parámetros almacenados al objeto del motor de tarifas.

        Importar el dominio aquí y no en el encabezado del módulo mantiene la
        dependencia acotada a quien la usa: el modelo se puede inspeccionar y
        migrar sin cargar el motor de cálculo.
        """
        from apps.estacionamiento.domain.tarifas import ParametrosTarifa

        return ParametrosTarifa(
            cuota_hora=self.cuota_hora,
            minutos_cortesia=self.minutos_cortesia,
            fraccion_minutos=self.fraccion_minutos,
            minutos_tolerancia=self.minutos_tolerancia,
            cobro_minimo=self.cobro_minimo,
            tope_diario=self.tope_diario,
        )

    @property
    def esta_abierto(self) -> bool:
        return self.estado == EstadoBoleto.ABIERTO

    @property
    def fin_de_ciclo(self):
        """
        Momento en que el boleto dejó de estar en curso, o `None` si sigue.

        No basta con `salida_en`: la cancelación de un folio registra el estado
        `CANCELADO` pero **no** una hora de salida, porque el vehículo no
        salió por caja. Un boleto cancelado tiene por tanto `salida_en` nulo, y
        tomarlo como criterio de cierre haría que su página pública no
        caducara nunca y que la depuración no lo alcanzara jamás.

        Para esos casos sirve `actualizado_en`, que es cuando la caja publicó
        la cancelación. No es la hora exacta del hecho, pero sí la del último
        cambio del boleto, que es lo que necesita una política de retención.
        """
        if self.estado == EstadoBoleto.ABIERTO:
            return None
        return self.salida_en or self.actualizado_en
