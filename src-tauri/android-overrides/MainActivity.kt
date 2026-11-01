package us.ad7np.airwsjtx

import android.webkit.WebView

class MainActivity : TauriActivity() {
    private var tts: TtsBridge? = null
    private var rtlSdr: RtlSdrBridge? = null

    override fun onWebViewCreate(webView: WebView) {
        super.onWebViewCreate(webView)
        val bridge = TtsBridge(this)
        tts = bridge
        webView.addJavascriptInterface(bridge, "AndroidTts")
        val receiver = RtlSdrBridge(this)
        rtlSdr = receiver
        webView.addJavascriptInterface(receiver, "AndroidRtlSdr")
    }

    override fun onDestroy() {
        tts?.shutdown()
        rtlSdr?.close()
        super.onDestroy()
    }
}
