package io.github.jhangyu.relume.ui

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import io.github.jhangyu.relume.install.AppVisibility
import io.github.jhangyu.relume.install.InstallRunner
import io.github.jhangyu.relume.sync.Notifications

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        Notifications.ensureChannels(this)
        InstallRunner.reconcileOnStart(this)
        setContent { RelumeRoot() }
    }

    override fun onStart() {
        super.onStart()
        AppVisibility.visible = true
    }

    override fun onStop() {
        AppVisibility.visible = false
        super.onStop()
    }
}
