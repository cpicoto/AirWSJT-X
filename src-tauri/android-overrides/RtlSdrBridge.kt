package us.ad7np.airwsjtx

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbManager
import android.os.Build
import android.webkit.JavascriptInterface
import org.json.JSONArray
import org.json.JSONObject

/**
 * Exposes attached RTL2832U receivers to the WebView as `window.AndroidRtlSdr`.
 * Android does not let native code open USB devices directly, so this grants
 * permission, opens the device, and hands its file descriptor to the Rust receiver.
 */
class RtlSdrBridge(context: Context) {
    private val appContext = context.applicationContext
    private val usb = appContext.getSystemService(Context.USB_SERVICE) as UsbManager
    private var connection: UsbDeviceConnection? = null

    private fun receivers(): List<UsbDevice> =
        usb.deviceList.values.filter { (it.vendorId shl 16 or it.productId) in KNOWN_DEVICES }

    private fun find(name: String): UsbDevice? = receivers().firstOrNull { it.deviceName == name }

    @JavascriptInterface
    fun listDevices(): String {
        val result = JSONArray()
        for (device in receivers()) {
            result.put(JSONObject()
                .put("name", device.deviceName)
                .put("vendorId", device.vendorId)
                .put("productId", device.productId)
                .put("product", device.productName ?: "")
                .put("manufacturer", device.manufacturerName ?: "")
                .put("hasPermission", usb.hasPermission(device)))
        }
        return result.toString()
    }

    @JavascriptInterface
    fun hasPermission(name: String): Boolean = find(name)?.let(usb::hasPermission) ?: false

    /** Shows the system USB permission prompt; poll [hasPermission] for the answer. */
    @JavascriptInterface
    fun requestPermission(name: String): Boolean {
        val device = find(name) ?: return false
        if (usb.hasPermission(device)) return true
        val intent = Intent(ACTION_PERMISSION).setPackage(appContext.packageName)
        // UsbManager fills in extras, so the intent must be mutable; it is explicit for Android 14+.
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0
        usb.requestPermission(device, PendingIntent.getBroadcast(appContext, 0, intent, flags))
        return false
    }

    /** Opens the receiver and returns its file descriptor, or -1. Call [close] after the receiver stops. */
    @JavascriptInterface
    @Synchronized
    fun open(name: String): Int {
        close()
        val device = find(name) ?: return -1
        if (!usb.hasPermission(device)) return -1
        val opened = usb.openDevice(device) ?: return -1
        connection = opened
        return opened.fileDescriptor
    }

    @JavascriptInterface
    @Synchronized
    fun close() {
        connection?.close()
        connection = null
    }

    companion object {
        private const val ACTION_PERMISSION = "us.ad7np.airwsjtx.USB_PERMISSION"

        /** RTL2832U VID/PID pairs from librtlsdr's known_devices table. */
        private val KNOWN_DEVICES: Set<Int> = listOf(
            0x0bda to 0x2832, 0x0bda to 0x2838, 0x0413 to 0x6680, 0x0413 to 0x6f0f, 0x0458 to 0x707f,
            0x0ccd to 0x00a9, 0x0ccd to 0x00b3, 0x0ccd to 0x00b4, 0x0ccd to 0x00b5, 0x0ccd to 0x00b7,
            0x0ccd to 0x00b8, 0x0ccd to 0x00b9, 0x0ccd to 0x00c0, 0x0ccd to 0x00c6, 0x0ccd to 0x00d3,
            0x0ccd to 0x00d7, 0x0ccd to 0x00e0, 0x1554 to 0x5020, 0x15f4 to 0x0131, 0x15f4 to 0x0133,
            0x185b to 0x0620, 0x185b to 0x0650, 0x185b to 0x0680, 0x1b80 to 0xd393, 0x1b80 to 0xd394,
            0x1b80 to 0xd395, 0x1b80 to 0xd397, 0x1b80 to 0xd398, 0x1b80 to 0xd39d, 0x1b80 to 0xd3a4,
            0x1b80 to 0xd3a8, 0x1b80 to 0xd3af, 0x1b80 to 0xd3b0, 0x1d19 to 0x1101, 0x1d19 to 0x1102,
            0x1d19 to 0x1103, 0x1d19 to 0x1104, 0x1f4d to 0xa803, 0x1f4d to 0xb803, 0x1f4d to 0xc803,
            0x1f4d to 0xd286, 0x1f4d to 0xd803,
        ).map { (vendor, product) -> vendor shl 16 or product }.toSet()
    }
}
