package io.github.jhangyu.relume.ui

import android.os.Build
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import io.github.jhangyu.relume.RelumeApp
import io.github.jhangyu.relume.api.AdbDevice
import io.github.jhangyu.relume.api.ApiError
import io.github.jhangyu.relume.api.DeviceInfo
import io.github.jhangyu.relume.api.InvalidServerUrl
import io.github.jhangyu.relume.api.RelumeApi
import io.github.jhangyu.relume.api.ServerUrl
import kotlinx.coroutines.launch
import java.io.IOException

private val LOGO_SIZE = 52.dp
private const val LOGO_LETTER = "R"

/** Registration answer held until the user decides about adopting an ADB row (token is stored only after that). */
private class Pending(val url: ServerUrl, val token: String, val adb: List<AdbDevice>)

/** Flat strings: url, token, then id/name/model per ADB row ("" model = none). Survives rotation. */
private val PendingSaver = listSaver<Pending?, String>(
    save = { p ->
        if (p == null) emptyList()
        else listOf(p.url.toString(), p.token) + p.adb.flatMap { listOf(it.id.toString(), it.name, it.model.orEmpty()) }
    },
    restore = { l ->
        if (l.isEmpty()) null
        else Pending(
            ServerUrl.parse(l[0]), l[1],
            l.drop(2).chunked(3).map { (id, name, model) -> AdbDevice(id.toInt(), name, model.ifEmpty { null }) },
        )
    },
)

/**
 * Frames 1 and 1b plus the ADB adopt choice. [notice] is shown on top (e.g. "This device was signed out by the server").
 * [onSignedIn] runs after the token is stored.
 */
@Composable
fun LoginScreen(app: RelumeApp, notice: String?, onSignedIn: () -> Unit) {
    val scope = rememberCoroutineScope()
    var urlText by rememberSaveable { mutableStateOf(app.prefs.serverUrl?.toString().orEmpty()) }
    var user by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var urlError by remember { mutableStateOf<String?>(null) }
    var unreachable by remember { mutableStateOf<Pair<String, String>?>(null) }
    var formError by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var pending by rememberSaveable(stateSaver = PendingSaver) { mutableStateOf<Pending?>(null) }

    fun finish(url: ServerUrl, token: String, adopt: Int?) {
        scope.launch {
            busy = true
            try {
                if (adopt != null) RelumeApi(url) { token }.adopt(adopt)
                app.signIn(url, token)
                onSignedIn()
            } catch (e: Exception) {
                formError = describe(e)
            } finally {
                busy = false
            }
        }
    }

    fun submit() {
        urlError = null
        unreachable = null
        formError = null
        val url = try {
            ServerUrl.parse(urlText)
        } catch (e: InvalidServerUrl) {
            urlError = e.message
            return
        }
        scope.launch {
            busy = true
            try {
                val api = RelumeApi(url) { null }
                try {
                    api.health()
                } catch (e: IOException) {
                    unreachable = "Can't reach $url" to describe(e)
                    return@launch
                }
                val device = DeviceInfo(
                    name = Build.MODEL,
                    model = Build.MODEL,
                    sdk = Build.VERSION.SDK_INT,
                    androidRelease = Build.VERSION.RELEASE,
                    abiList = Build.SUPPORTED_ABIS.toList(),
                )
                val reg = api.registerDevice(user.trim(), password, device)
                if (reg.adbDevices.isEmpty()) {
                    finish(url, reg.token, null)
                } else {
                    pending = Pending(url, reg.token, reg.adbDevices)
                }
            } catch (e: ApiError) {
                formError = describe(e)
            } catch (e: IOException) {
                formError = describe(e)
            } finally {
                busy = false
            }
        }
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Box(
            Modifier.padding(top = 30.dp).size(LOGO_SIZE).clip(RoundedCornerShape(14.dp)).background(MaterialTheme.colorScheme.primary),
            contentAlignment = Alignment.Center,
        ) { Txt(LOGO_LETTER, style = MaterialTheme.typography.headlineSmall, color = MaterialTheme.colorScheme.onPrimary) }
        Txt("Connect to Relume", style = MaterialTheme.typography.headlineSmall)
        val choice = pending
        if (choice != null) {
            AdoptChoice(choice, busy, onAdopt = { finish(choice.url, choice.token, it) }, onNew = { finish(choice.url, choice.token, null) })
            formError?.let { Banner("Sign-in failed", Tone.Danger, detail = it) }
            return@Column
        }
        Txt("Enter your Relume server address and sign in with the admin account.", color = muted())
        notice?.let { Banner(it, Tone.Warn) }
        unreachable?.let { (head, detail) -> Banner(head, Tone.Danger, detail = detail, action = "Retry", onAction = ::submit) }
        formError?.let { Banner("Sign-in failed", Tone.Danger, detail = it) }
        Field(
            "Server URL", urlText, { urlText = it }, mono = true, error = urlError,
            hint = "https:// recommended · works over Tailscale or a reverse proxy",
            keyboard = KeyboardOptions(keyboardType = KeyboardType.Uri),
        )
        Field("Username", user, { user = it })
        Field("Password", password, { password = it }, secret = true)
        ActionButton("Sign in", ::submit, Modifier.fillMaxWidth(), kind = ButtonKind.Primary, enabled = !busy && user.isNotBlank() && password.isNotEmpty() && urlText.isNotBlank())
        Txt(
            "Signing in registers this phone and stores a per-device token. Revoke it any time from the server.",
            style = MaterialTheme.typography.bodyMedium, color = muted(),
        )
    }
}

@Composable
private fun AdoptChoice(pending: Pending, busy: Boolean, onAdopt: (Int) -> Unit, onNew: () -> Unit) {
    Txt("Is this phone already on the server as an ADB device? Choosing one keeps its app assignments.", color = muted())
    pending.adb.forEach { d ->
        InsetGroup(Modifier.clickable(enabled = !busy) { onAdopt(d.id) }) {
            ListRow(d.name, supporting = d.model)
        }
    }
    ActionButton("No, register as a new device", onNew, Modifier.fillMaxWidth(), kind = ButtonKind.Outlined, enabled = !busy)
}
