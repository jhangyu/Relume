package io.github.jhangyu.relume

import android.app.Application
import io.github.jhangyu.relume.api.Prefs
import io.github.jhangyu.relume.api.RelumeApi
import io.github.jhangyu.relume.api.ServerUrl
import io.github.jhangyu.relume.api.TokenStore

class RelumeApp : Application() {
    lateinit var prefs: Prefs
        private set
    lateinit var tokenStore: TokenStore
        private set

    /** Null before login and after logout. */
    @Volatile
    var api: RelumeApi? = null
        private set

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        tokenStore = TokenStore(this)
        val url = prefs.serverUrl
        if (url != null && tokenStore.hasToken()) api = RelumeApi(url, tokenStore::read)
    }

    /** After `POST /api/auth/device`: persist the server and token, then expose the signed-in client. */
    fun signIn(url: ServerUrl, token: String) {
        prefs.serverUrl = url
        tokenStore.write(token)
        api = RelumeApi(url, tokenStore::read)
    }

    /** After logout or a 401: drop the token; the server URL stays for the next login. */
    fun signOut() {
        tokenStore.clear()
        api = null
    }
}
