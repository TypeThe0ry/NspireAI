import com.ti.eps.navnet.ConnectionHandle;
import com.ti.eps.navnet.Context;
import com.ti.eps.navnet.IntegerBox;
import com.ti.eps.navnet.NodeHandle;
import com.ti.eps.navnet.NodeNotificationListener;
import com.ti.eps.navnet.ServiceCallbackListener;
/* Use the public client facade.  The server-side class has the same method
 * names but calls libnavnet.dylib directly; using it from this process skips
 * Student Software's RemoteNavnetServer and crashes while registering a
 * service. */
import com.ti.eps.navnet.NavNet;
import com.ti.et.navnetcommproxy.NavNetCommProxy;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;

/**
 * Small stdin/stdout adapter around TI's own macOS NavNet host library.
 *
 * The calculator-side Ndless extension is a NavNet client (TI_NN_Connect),
 * while this process exposes service 0x5001 and keeps the connection alive.
 * stdout is deliberately line-oriented because bridge/navnet_bridge.py owns
 * framing and backend state.
 */
public final class NspireNavnetHelper {
    private static final Object IO_LOCK = new Object();
    private static volatile ConnectionHandle connection;
    private static volatile boolean stopping;
    private static volatile Thread reader;

    private static String hex(byte[] data, int length) {
        StringBuilder out = new StringBuilder(length * 2);
        for (int i = 0; i < length; i++) {
            out.append(String.format("%02x", data[i] & 0xff));
        }
        return out.toString();
    }

    private static byte[] unhex(String value) {
        if ((value.length() & 1) != 0) throw new IllegalArgumentException("odd hex length");
        byte[] out = new byte[value.length() / 2];
        for (int i = 0; i < out.length; i++) {
            int hi = Character.digit(value.charAt(i * 2), 16);
            int lo = Character.digit(value.charAt(i * 2 + 1), 16);
            if (hi < 0 || lo < 0) throw new IllegalArgumentException("invalid hex");
            out[i] = (byte) ((hi << 4) | lo);
        }
        return out;
    }

    private static void emit(String line) {
        System.out.println(line);
        System.out.flush();
    }

    private static void write(ConnectionHandle handle, byte[] data) {
        synchronized (IO_LOCK) {
            int status = NavNet.write(handle, data, data.length);
            if (status < 0) emit("ERR NavNet.write=" + status);
        }
    }

    private static void startReader(ConnectionHandle handle) {
        if (reader != null && reader.isAlive()) return;
        reader = new Thread(() -> {
            byte[] buffer = new byte[4096];
            while (!stopping && connection == handle) {
                IntegerBox received = new IntegerBox();
                int status;
                synchronized (IO_LOCK) {
                    status = NavNet.read(handle, 200L, buffer, received);
                }
                if (status < 0) {
                    if (!stopping) emit("ERR NavNet.read=" + status);
                    break;
                }
                int length = received.getValue();
                if (length > 0) emit("RX " + hex(buffer, Math.min(length, buffer.length)));
            }
        }, "nspire-navnet-reader");
        reader.setDaemon(true);
        reader.start();
    }

    public static void main(String[] args) throws Exception {
        final int serviceId = Integer.decode(System.getenv().getOrDefault(
                "NSPIRE_SERVICE_ID", "0x4051"));
        String tmp = System.getProperty("java.io.tmpdir");
        String javaHome = System.getProperty("java.home") + "/bin";
        NavNetCommProxy proxy;
        try {
            /* The proxy supplies the connector path and macOS native-loader
             * setup that TI's Student Software expects.  Calling NavNet.init
             * with only "-c/-d" leaves the connector directory unset and
             * makes startService return -281. */
            proxy = NavNetCommProxy.init("NspireAI", tmp, 0, 0, javaHome, "");
        } catch (Exception error) {
            emit("ERR NavNetCommProxy.init=" + error);
            return;
        }
        int status;
        try {
            NavNet.registerNotifyCallback(new NodeNotificationListener() {
                @Override public void nodeNotificationCallback(NodeHandle node, int event) {
                    emit("NODE " + event);
                }
            });
            status = NavNet.startService(serviceId, new Context(), new ServiceCallbackListener() {
                @Override public void serviceCallback(ConnectionHandle handle, Context context) {
                    connection = handle;
                    emit("CONNECTED");
                    // Request a harmless bootstrap packet.  The Lua page
                    // answers it with PONG after its native timer runs.
                    write(handle, new byte[] {'N','S','A','I',1,1,0,0,0,0,0,0,0,5,'H','E','L','L','O'});
                    startReader(handle);
                }
            });
            if (status < 0) {
                emit("ERR NavNet.startService=" + status);
                return;
            }
            emit(String.format("READY service=0x%04x", serviceId));
            BufferedReader input = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
            String line;
            while (!stopping && (line = input.readLine()) != null) {
                line = line.trim();
                if (line.equals("QUIT")) break;
                if (!line.startsWith("SEND ")) continue;
                ConnectionHandle handle = connection;
                if (handle == null) {
                    emit("ERR not connected");
                    continue;
                }
                try {
                    write(handle, unhex(line.substring(5).trim()));
                    emit("OK");
                } catch (RuntimeException error) {
                    emit("ERR " + error.getMessage());
                }
            }
        } finally {
            stopping = true;
            ConnectionHandle handle = connection;
            if (handle != null) {
                try { NavNet.disconnect(handle); } catch (RuntimeException ignored) { }
            }
            try { NavNet.stopService(serviceId); } catch (RuntimeException ignored) { }
            try { NavNet.unregisterNotifyCallback(); } catch (RuntimeException ignored) { }
            try { proxy.shutdown(); } catch (RuntimeException ignored) { }
        }
    }
}
