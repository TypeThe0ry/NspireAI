import com.ti.eps.navnet.ConnectionHandle;
import com.ti.eps.navnet.Context;
import com.ti.eps.navnet.IntegerBox;
import com.ti.eps.navnet.NodeHandle;
import com.ti.eps.navnet.NodeNotificationListener;
import com.ti.eps.navnet.ServiceCallbackListener;
import com.ti.et.education.commproxy.INodeID;
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
 * The calculator-side standalone Ndless program is a NavNet client
 * (TI_NN_Connect), while this process exposes the project's private service
 * 0x5001 and keeps the connection alive.  The TI Java host accepts this
 * custom service; 0x8001 is rejected by the same host with -281.
 * stdout is deliberately line-oriented because bridge/navnet_bridge.py owns
 * framing and backend state.
 */
public final class NspireNavnetHelper {
    private static final Object IO_LOCK = new Object();
    // TI_NN_ERR_INVALID_CONNECTION.  During a handheld-side service startup
    // the Java host can report this transiently before the connection handle
    // is usable; the reader must keep the bridge alive and let a later
    // callback replace the handle instead of terminating on the first read.
    private static final int ERR_INVALID_CONNECTION = -257;
    private static volatile ConnectionHandle connection;
    private static volatile boolean stopping;
    private static volatile Thread reader;
    private static volatile Thread connectionWatcher;
    private static volatile Thread nodePoller;
    private static volatile boolean nodePresent;
    private static volatile int bootstrapServiceId;
    private static volatile ConnectionHandle bootstrapConnection;
    private static volatile Thread bootstrapThread;

    /*
     * TI's macOS NavNet 6.2 native server has a reproducible teardown crash:
     * RemoteNavnetServer.stopService(0x5001) can dereference a null service
     * slot after the USB connection has gone away.  The shell wrapper removes
     * a server created by this invocation, so the safe default is to release
     * the connection and leave stopService untouched.  Set this only for a
     * controlled TI-runtime experiment; normal bridge shutdown must not call
     * the crashing native path.
     */
    private static boolean shouldStopService() {
        return "1".equals(System.getenv().getOrDefault(
                "NSPIRE_NAVNET_STOP_SERVICE", "0"));
    }

    private static void armShutdownWatchdog() {
        Thread watchdog = new Thread(() -> {
            try {
                Thread.sleep(8000L);
            } catch (InterruptedException ignored) {
                return;
            }
            // TI's RMI/native shutdown occasionally logs success but leaves
            // non-daemon threads alive.  The shell wrapper separately removes
            // only a RemoteNavnetServer created by this invocation.
            Runtime.getRuntime().halt(124);
        }, "nspire-navnet-shutdown-watchdog");
        watchdog.setDaemon(true);
        watchdog.start();
    }

    private static void shutdownProxyBestEffort(NavNetCommProxy proxy) {
        Thread shutdown = new Thread(() -> {
            try {
                proxy.shutdown();
            } catch (RuntimeException ignored) {
                // The shell wrapper owns the detached RemoteNavnetServer and
                // removes only the child created by this invocation.
            }
        }, "nspire-navnet-proxy-shutdown");
        shutdown.setDaemon(true);
        shutdown.start();
        try {
            // TI's native/RMI shutdown can block indefinitely after a USB
            // connector has been loaded. Give it a small grace period, then
            // let the hard halt below keep the helper lifecycle bounded.
            shutdown.join(500L);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }

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
            try {
                boolean invalidReported = false;
                while (!stopping && connection == handle) {
                    IntegerBox received = new IntegerBox();
                    int status;
                    synchronized (IO_LOCK) {
                        status = NavNet.read(handle, 200L, buffer, received);
                    }
                    if (status < 0) {
                        if (stopping || connection != handle) break;
                        if (status == ERR_INVALID_CONNECTION) {
                            if (!invalidReported) {
                                emit("ERR NavNet.read=" + status + "; retrying");
                                invalidReported = true;
                            }
                            try {
                                Thread.sleep(100L);
                            } catch (InterruptedException interrupted) {
                                Thread.currentThread().interrupt();
                                break;
                            }
                            continue;
                        }
                        emit("ERR NavNet.read=" + status);
                        break;
                    }
                    invalidReported = false;
                    int length = received.getValue();
                    if (length > 0) emit("RX " + hex(buffer, Math.min(length, buffer.length)));
                }
            } finally {
                // Let the watcher start a fresh reader if the service callback
                // replaced this handle while the old read was unwinding.
                if (Thread.currentThread() == reader) reader = null;
            }
        }, "nspire-navnet-reader");
        reader.setDaemon(true);
        reader.start();
    }

    private static void startConnectionWatcher() {
        if (connectionWatcher != null && connectionWatcher.isAlive()) return;
        connectionWatcher = new Thread(() -> {
            ConnectionHandle observed = null;
            while (!stopping) {
                ConnectionHandle handle = connection;
                boolean readerMissing = reader == null || !reader.isAlive();
                if (handle != null && (handle != observed || readerMissing)) {
                    observed = handle;
                    try {
                        // The service callback may run just before
                        // startService() returns, or concurrently with this
                        // watcher on a later reconnect. Never enter TI's read
                        // path until the callback has had time to unwind.
                        Thread.sleep(readerMissing ? 150L : 50L);
                    } catch (InterruptedException ignored) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                    if (!stopping && connection == handle &&
                            (reader == null || !reader.isAlive())) startReader(handle);
                }
                try {
                    Thread.sleep(50L);
                } catch (InterruptedException ignored) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }, "nspire-navnet-connection-watcher");
        connectionWatcher.setDaemon(true);
        connectionWatcher.start();
    }

    /**
     * TI's RMI server can already know about a handheld when a new client
     * registers its callback.  In that case the callback is not guaranteed to
     * replay an ADD event, leaving the bridge at READY forever even though the
     * USB device is still 0xE022.  Poll the public connected-node list as a
     * read-only reconciliation path; this never opens a calculator channel or
     * invokes a device syscall.
     */
    private static void startNodePoller(NavNetCommProxy proxy) {
        if (nodePoller != null && nodePoller.isAlive()) return;
        nodePoller = new Thread(() -> {
            while (!stopping) {
                try {
                    INodeID[] nodes = proxy.getConnectedNodes();
                    boolean present = nodes != null && nodes.length > 0;
                    if (present != nodePresent) {
                        nodePresent = present;
                        emit("NODE " + (present ? "1" : "0"));
                        if (present && bootstrapServiceId > 0 && nodes != null) {
                            try {
                                startBootstrap(proxy.getHandle(nodes[0]));
                            } catch (RuntimeException ignored) {
                                // A transient RMI node snapshot can disappear
                                // between getConnectedNodes/getHandle; the
                                // next state transition or callback retries.
                            }
                        }
                    }
                } catch (Exception ignored) {
                    // The callback remains authoritative when the RMI server
                    // is between reconnects; retry on the next poll interval.
                }
                try {
                    Thread.sleep(500L);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }, "nspire-navnet-node-poller");
        nodePoller.setDaemon(true);
        nodePoller.start();
    }

    /*
     * Candidate-only handshake for the calculator's separate local service.
     * The historical TI test calls the calculator service from the PC before
     * the calculator connects back to the PC service.  Keep this off by
     * default: the production bridge must not open an extra NavNet channel.
     */
    private static synchronized void startBootstrap(NodeHandle node) {
        if (bootstrapServiceId <= 0 || node == null || stopping) return;
        if (bootstrapThread != null && bootstrapThread.isAlive()) return;
        bootstrapThread = new Thread(() -> {
            try {
                /* The node can be visible before the calculator reaches the
                 * Menu arm and registers 0x5002. Retry only in this explicit
                 * candidate mode, for a bounded window, so the normal helper
                 * never adds another NavNet call or timer. */
                for (int attempt = 0; attempt < 40 && !stopping; attempt++) {
                    ConnectionHandle handle = new ConnectionHandle();
                    try {
                        int status = NavNet.connect(node, bootstrapServiceId, handle);
                        emit(String.format("BOOTSTRAP CONNECT service=0x%04x attempt=%d status=%d",
                                bootstrapServiceId, attempt + 1, status));
                        if (status >= 0) {
                            bootstrapConnection = handle;
                            byte[] request = "NSAI bootstrap".getBytes(StandardCharsets.US_ASCII);
                            int writeStatus = NavNet.write(handle, request, request.length);
                            emit("BOOTSTRAP WRITE status=" + writeStatus);
                            if (writeStatus >= 0) {
                                byte[] response = new byte[64];
                                IntegerBox received = new IntegerBox();
                                int readStatus = NavNet.read(handle, 1000L, response, received);
                                emit("BOOTSTRAP RX status=" + readStatus +
                                        " length=" + received.getValue());
                            }
                            return;
                        }
                    } finally {
                        if (bootstrapConnection == handle) bootstrapConnection = null;
                        try { NavNet.disconnect(handle); } catch (RuntimeException ignored) { }
                    }
                    try {
                        Thread.sleep(250L);
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                }
            } catch (RuntimeException error) {
                emit("BOOTSTRAP ERR " + error.getMessage());
            } finally {
                bootstrapThread = null;
            }
        }, "nspire-navnet-bootstrap");
        bootstrapThread.setDaemon(true);
        bootstrapThread.start();
    }

    public static void main(String[] args) throws Exception {
        final int serviceId = Integer.decode(System.getenv().getOrDefault(
                "NSPIRE_SERVICE_ID", "0x5001"));
        bootstrapServiceId = Integer.decode(System.getenv().getOrDefault(
                "NSPIRE_BOOTSTRAP_SERVICE_ID", "0"));
        String tmp = System.getProperty("java.io.tmpdir");
        String javaHome = System.getProperty("java.home") + "/bin";
        NavNetCommProxy proxy;
        try {
            /* The proxy supplies the connector path and macOS native-loader
             * setup that TI's Student Software expects.  Calling NavNet.init
             * with only "-c/-d" leaves the connector directory unset and
             * makes startService return -281. */
            int logLevel = Integer.parseInt(System.getenv().getOrDefault(
                    "NSPIRE_NAVNET_LOG_LEVEL", "0"));
            if (logLevel < 0 || logLevel > 3) {
                throw new IllegalArgumentException("NSPIRE_NAVNET_LOG_LEVEL must be 0..3");
            }
            proxy = NavNetCommProxy.init("NspireAI", tmp, logLevel, logLevel, javaHome, "");
        } catch (Exception error) {
            emit("ERR NavNetCommProxy.init=" + error);
            return;
        }
        int status;
        try {
            /* NavNet.init() establishes the RMI client, but the TI Java API
             * exposes connector loading as a separate operation.  Student
             * Software normally performs this during its own startup; a
             * standalone helper must do it explicitly or it can report READY
             * while getConnectedNodes() remains empty even though macOS sees
             * the CX II USB descriptor. */
            int connectorStatus = NavNet.loadConnectors();
            emit("CONNECTORS status=" + connectorStatus);
            if (connectorStatus < 0) {
                emit("ERR NavNet.loadConnectors=" + connectorStatus);
                return;
            }
            NavNet.registerNotifyCallback(new NodeNotificationListener() {
                @Override public void nodeNotificationCallback(NodeHandle node, int event) {
                    nodePresent = event != 0;
                    emit("NODE " + event);
                    if (event != 0) startBootstrap(node);
                }
            });
            status = NavNet.startService(serviceId, new Context(), new ServiceCallbackListener() {
                @Override public void serviceCallback(ConnectionHandle handle, Context context) {
                    connection = handle;
                    emit(handle == null
                            ? "CONNECTED handle=null"
                            : "CONNECTED handle=0x" + Long.toHexString(handle.getCPtr()));
                    // No NavNet call is allowed from this callback. In
                    // particular, read/write here can re-enter TI's callback
                    // path and deadlock before startService() returns.
                }
            });
            if (status < 0) {
                emit("ERR NavNet.startService=" + status);
                return;
            }
            emit(String.format("READY service=0x%04x", serviceId));
            startConnectionWatcher();
            startNodePoller(proxy);
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
            armShutdownWatchdog();
            ConnectionHandle handle = connection;
            if (handle != null) {
                try { NavNet.disconnect(handle); } catch (RuntimeException ignored) { }
            }
            ConnectionHandle bootstrap = bootstrapConnection;
            if (bootstrap != null) {
                try { NavNet.disconnect(bootstrap); } catch (RuntimeException ignored) { }
            }
            if (shouldStopService()) {
                try { NavNet.stopService(serviceId); } catch (RuntimeException ignored) { }
            }
            try { NavNet.unregisterNotifyCallback(); } catch (RuntimeException ignored) { }
            // Report the protocol endpoint as stopped before entering TI's
            // potentially blocking native teardown. The wrapper's EXIT trap
            // still removes the detached server if the best-effort thread is
            // cut short by the bounded halt.
            emit("STOPPED");
            shutdownProxyBestEffort(proxy);
            // Avoid running TI's shutdown hook a second time and guarantee
            // that no RMI client thread keeps the helper alive after cleanup.
            Runtime.getRuntime().halt(0);
        }
    }
}
