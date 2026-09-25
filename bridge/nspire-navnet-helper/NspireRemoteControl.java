import com.ti.et.education.commproxy.ICommproxyNodeScreen;
import com.ti.et.education.commproxy.IEvent;
import com.ti.et.education.commproxy.INodeID;
import com.ti.et.education.commproxy.INodeInfo;
import com.ti.et.education.commproxy.NspireVirtualKeyStroke;
import com.ti.et.navnetcommproxy.NavNetCommProxy;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.File;
import java.util.Arrays;

/** Diagnostic/control client for TI's standard NavNet event and screen APIs. */
public final class NspireRemoteControl {
    private static final int WAIT_MS = positiveEnv("NSPIRE_NODE_WAIT_MS", 30000);
    private static final int POLL_MS = 250;

    private static int positiveEnv(String name, int fallback) {
        String value = System.getenv(name);
        if (value == null || value.isEmpty()) return fallback;
        try {
            int parsed = Integer.parseInt(value);
            if (parsed > 0) return parsed;
        } catch (NumberFormatException ignored) {
            // Keep the safe default; the CLI still remains usable when an
            // inherited environment contains an invalid value.
        }
        return fallback;
    }

    private static INodeID waitForNode(NavNetCommProxy proxy) throws Exception {
        long deadline = System.currentTimeMillis() + WAIT_MS;
        while (System.currentTimeMillis() < deadline) {
            INodeID[] nodes = proxy.getConnectedNodes();
            if (nodes != null && nodes.length > 0) return nodes[0];
            Thread.sleep(POLL_MS);
        }
        throw new IllegalStateException("no connected TI-Nspire node within " + WAIT_MS + " ms");
    }

    private static void printInfo(NavNetCommProxy proxy, INodeID node) throws Exception {
        INodeInfo info = proxy.getNodeInfo(node);
        System.out.println("NODE id=" + node.getIDDisplayString()
                + " name=" + info.getName()
                + " serial=" + info.getSerialNumber()
                + " electronicId=" + info.getElectronicId()
                + " runLevel=" + info.getRunLevel()
                + " connectionType=" + info.getConnectionType());
    }

    private static void saveScreen(NavNetCommProxy proxy, INodeID node, String path) throws Exception {
        Object value = proxy.getScreen(node, true);
        if (value instanceof ICommproxyNodeScreen) value = ((ICommproxyNodeScreen) value).getScreen();
        if (!(value instanceof BufferedImage)) {
            throw new IllegalStateException("TI screen API returned "
                    + (value == null ? "null" : value.getClass().getName()));
        }
        File output = new File(path);
        File parent = output.getParentFile();
        if (parent != null) parent.mkdirs();
        ImageIO.write((BufferedImage) value, "png", output);
        System.out.println("SCREEN path=" + output.getAbsolutePath() + " size=" + output.length());
    }

    private static void sendKey(NavNetCommProxy proxy, INodeID node, String key) throws Exception {
        IEvent event = new NspireVirtualKeyStroke(key);
        proxy.sendEventToNode(node, event);
        System.out.println("KEY " + key);
        Thread.sleep(100L);
    }

    private static void usage() {
        System.err.println("usage: NspireRemoteControl info|screen <png>|key <key>...|type <text>|list <dir>|upload <local.tns> <remote.tns>");
        System.err.println("key names use TI tokens such as ~home~, ~menu~, ~down~, ~enter~");
        System.err.println("diagnostics: download <remote> <absolute-new-local-path>|verify-program <local.tns>|delete-legacy <approved-path>...|remove-failed-program");
    }

    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            usage();
            System.exit(2);
        }
        String tmp = System.getProperty("java.io.tmpdir");
        String javaHome = System.getProperty("java.home") + "/bin";
        NavNetCommProxy proxy = NavNetCommProxy.init("NspireAI Remote", tmp, 0, 0, javaHome, "");
        int exitCode = 0;
        try {
            INodeID node = waitForNode(proxy);
            printInfo(proxy, node);
            String command = args[0];
            if (command.equals("info")) return;
            if (command.equals("download") && args.length == 3) {
                java.nio.file.Path destination = new File(args[2]).toPath();
                if (!args[1].startsWith("/") || !destination.isAbsolute()) {
                    throw new IllegalArgumentException("download requires absolute remote and local paths");
                }
                // CREATE_NEW reserves the output without replacing user data.
                java.nio.file.Files.createFile(destination);
                boolean complete = false;
                try {
                    proxy.receiveFileFromNode(node, args[1], destination.toString());
                    byte[] bytes = java.nio.file.Files.readAllBytes(destination);
                    byte[] hash = java.security.MessageDigest.getInstance("SHA-256").digest(bytes);
                    StringBuilder hex = new StringBuilder();
                    for (byte value : hash) hex.append(String.format("%02x", value & 255));
                    System.out.println("DOWNLOADED " + args[1] + " bytes=" + bytes.length
                            + " sha256=" + hex + " local=" + destination);
                    complete = true;
                } finally {
                    if (!complete) java.nio.file.Files.deleteIfExists(destination);
                }
                return;
            }
            if (command.equals("remove-failed-program") && args.length == 1) {
                // Single explicit recovery target; never recursively delete.
                proxy.deleteFileFromNode(node, "/nspire_ai.tns");
                System.out.println("DELETED /nspire_ai.tns (failed runtime candidate)");
                return;
            }
            if (command.equals("verify-program") && args.length == 2) {
                java.nio.file.Path expected = new File(args[1]).toPath();
                if (!java.nio.file.Files.isRegularFile(expected)) {
                    throw new IllegalArgumentException("local program must exist");
                }
                java.nio.file.Path received = java.nio.file.Files.createTempFile("nspire-verify-", ".tns");
                try {
                    proxy.receiveFileFromNode(node, "/nspire_ai.tns", received.toString());
                    byte[] actual = java.nio.file.Files.readAllBytes(received);
                    byte[] wanted = java.nio.file.Files.readAllBytes(expected);
                    if (!Arrays.equals(actual, wanted)) throw new IllegalStateException("program readback differs");
                    byte[] hash = java.security.MessageDigest.getInstance("SHA-256").digest(actual);
                    StringBuilder hex = new StringBuilder();
                    for (byte value : hash) hex.append(String.format("%02x", value & 255));
                    System.out.println("VERIFIED /nspire_ai.tns bytes=" + actual.length + " sha256=" + hex);
                } finally {
                    java.nio.file.Files.deleteIfExists(received);
                }
                return;
            }
            if (command.equals("delete-legacy") && args.length >= 2) {
                java.util.Set<String> allowed = new java.util.HashSet<>(Arrays.asList(
                        "/AI.tns", "/AI-ui-demo.tns", "/nspire_ai.luax.tns", "/nspire_ai_nav.luax.tns",
                        "/ndless/nspire_ai.luax.tns",
                        "/nspireai/.exchange.body.tmp", "/nspireai/request.tns",
                        "/nspireai/request.id.tns", "/nspireai/response.tns", "/nspireai/response.id.tns"));
                for (String path : Arrays.copyOfRange(args, 1, args.length)) {
                    if (!allowed.contains(path)) throw new IllegalArgumentException("not an approved legacy artifact: " + path);
                }
                for (String path : Arrays.copyOfRange(args, 1, args.length)) {
                    proxy.deleteFileFromNode(node, path);
                    System.out.println("DELETED " + path);
                }
                return;
            }
            if (command.equals("list") && args.length == 2) {
                for (com.ti.et.navnetcommproxy.FileAttributesProxy entry : proxy.enumDirectory(node, args[1])) {
                    String name = entry.getName();
                    if (name == null && entry.getNameAsBytes() != null) {
                        name = new String(entry.getNameAsBytes(), java.nio.charset.StandardCharsets.UTF_8)
                                .replace("\u0000", "");
                    }
                    System.out.println((entry.isDirectory() ? "DIR " : "FILE ")
                            + entry.getSize() + " " + name);
                }
                return;
            }
            if (command.equals("upload") && args.length == 3) {
                File source = new File(args[1]);
                if (!source.isFile() || !source.getName().endsWith(".tns")
                        || !args[2].endsWith(".tns")) {
                    throw new IllegalArgumentException("upload requires an existing .tns and a .tns destination");
                }
                byte[] digest = java.security.MessageDigest.getInstance("SHA-256")
                        .digest(java.nio.file.Files.readAllBytes(source.toPath()));
                StringBuilder artifactHash = new StringBuilder();
                for (byte value : digest) artifactHash.append(String.format("%02x", value & 255));
                String artifactSha = artifactHash.toString();
                java.nio.file.Path manifest = source.toPath().resolveSibling(source.getName() + ".meta");
                if (!java.nio.file.Files.isRegularFile(manifest))
                    throw new IllegalArgumentException("missing successful-build manifest");
                java.util.List<String> manifestLines = java.nio.file.Files.readAllLines(manifest,
                        java.nio.charset.StandardCharsets.UTF_8);
                if (!manifestLines.contains("build_status=success") ||
                        !manifestLines.contains("sha256=" + artifactSha))
                    throw new IllegalArgumentException("artifact/manifest mismatch");
                if (!manifestLines.contains("ui_backend=TRUE") &&
                        !manifestLines.contains("ui_backend=FALSE"))
                    throw new IllegalArgumentException("manifest missing valid ui_backend");
                boolean irqWindow = manifestLines.contains("ngc_irq_window=TRUE");
                if (!irqWindow && !manifestLines.contains("ngc_irq_window=FALSE"))
                    throw new IllegalArgumentException("manifest missing valid ngc_irq_window");
                if (irqWindow && !"1".equals(System.getenv("NSPIRE_ALLOW_NGC_IRQ_WINDOW_UPLOAD")))
                    throw new IllegalArgumentException("refusing opt-in IRQ-window candidate without explicit upload confirmation");
                boolean cpuIrq = manifestLines.contains("ngc_cpu_irq=TRUE");
                if (!cpuIrq && !manifestLines.contains("ngc_cpu_irq=FALSE"))
                    throw new IllegalArgumentException("manifest missing valid ngc_cpu_irq");
                if (cpuIrq)
                    throw new IllegalArgumentException("refusing CPU-IRQ candidate: CX II flashed once and froze after launch on 2026-09-25; no override is permitted");
                if (artifactSha.equals("6fbafc2c81be00e05baf62c898b895e3cbce4a0f6bddd58c5730254369759238"))
                    throw new IllegalArgumentException("refusing CPU-IRQ auto-transport candidate: handheld flashed once and froze after launch on 2026-09-25");
                boolean irqMenu = manifestLines.contains("ngc_irq_menu=TRUE");
                if (!irqMenu && !manifestLines.contains("ngc_irq_menu=FALSE"))
                    throw new IllegalArgumentException("manifest missing valid ngc_irq_menu");
                if (irqMenu)
                    throw new IllegalArgumentException("refusing physically-crashing Menu-gated IRQ candidate; rebuild with ngc_irq_menu=FALSE");
                if (artifactSha.equals("ce92ae85e1d60cf9c3d4fea08ff1e897d35e13718cafd0ce23080fddd9e13c6c"))
                    throw new IllegalArgumentException("refusing known-crashing IRQ-scope 0922 build");
                if (artifactSha.equals("53f5f14965d3c4280f86f82565f22916db7eabbace8f6b7b9a4c410b4d94db2e"))
                    throw new IllegalArgumentException("refusing SDL candidate that stalled the handheld on 2026-09-23");
                if (artifactSha.equals("c8c564c2910a2f907fc792b47329a591cbc93dcbfc9e8f61327e73d2ac75aadf"))
                    throw new IllegalArgumentException("refusing physically-crashing Menu-gated IRQ candidate tested on 2026-09-24");
                if (artifactSha.equals("bc2c2099934f622cf0b3c137f7bb46416f04c1c45f9935f2351f03763bdc495f"))
                    throw new IllegalArgumentException("refusing NGC candidate that stalled handheld startup on 2026-09-23");
                if (artifactSha.equals("e0282e26c2d5017b76e893a15d51aa8c44a78f94cebcbf23a8d1ff651029d75c"))
                    throw new IllegalArgumentException("refusing NGC lcd_blit candidate: launch returned handheld to Home and TI logged a CX II Data Abort on 2026-09-23");
                if (artifactSha.equals("51ca73922afbc0ba2f0b488a98f4ff703eaa8081c64edac951ba1335d833a301"))
                    throw new IllegalArgumentException("refusing NGC relocation candidate: handheld rejected it as unsupported document format on 2026-09-24");
                if (artifactSha.equals("5f3d5213ccc3ff5ef60054981541df03565f69b943ac734f0ea73dafeea62cc9"))
                    throw new IllegalArgumentException("refusing SDK-wrapper NGC candidate: handheld rejected it as unsupported document format on 2026-09-24");
                if (artifactSha.equals("b821080614c2d3eb839b38f8a1f45105485f7a4bee26f49dea149bba82e42d20"))
                    throw new IllegalArgumentException("refusing NGC lcd-order candidate: handheld rejected it as unsupported document format on 2026-09-24");
                if (artifactSha.equals("e9e4938138cdaee41f0fb86c8a858e3356ed4a8066615e658df36fba1b1d21ec")
                        && !"1".equals(System.getenv("NSPIRE_ALLOW_NGC_ENTRY_PROBE_UPLOAD")))
                    throw new IllegalArgumentException("refusing NGC entry probe without explicit upload confirmation");
                if (artifactSha.equals("66f822dab397de642ea7d7f723451a8c1d1c8f944862c5e7808646fab7045a05")
                        && !"1".equals(System.getenv("NSPIRE_ALLOW_NGC_ENTRY_STAGE1_UPLOAD")))
                    throw new IllegalArgumentException("refusing NGC entry stage-1 probe without explicit upload confirmation");
                if (artifactSha.equals("cb1002135d4f669b71dd0fb0f5023e66c2459eb682905a194196d79f49786999")
                        && !"1".equals(System.getenv("NSPIRE_ALLOW_NGC_ENTRY_STAGE2_UPLOAD")))
                    throw new IllegalArgumentException("refusing NGC entry stage-2 probe without explicit upload confirmation");
                if (artifactSha.equals("b96560ea025b9328a7ab98bd8552c28015a658ef872ba22fd178a5debd759245")
                        && !"1".equals(System.getenv("NSPIRE_ALLOW_NGC_ENTRY_STAGE3_UPLOAD")))
                    throw new IllegalArgumentException("refusing NGC entry stage-3 probe without explicit upload confirmation");
                if (artifactSha.equals("649f5dee4e58d643839c5c120e66af51b3f508e621e24c3f3bdf262a4bc00a94")
                        && !"1".equals(System.getenv("NSPIRE_ALLOW_NGC_ENTRY_STAGE4_UPLOAD")))
                    throw new IllegalArgumentException("refusing NGC entry stage-4 probe without explicit upload confirmation");
                if (artifactSha.equals("8b713d32771211438be2e4ac28f53d8e80fa4f9bd3aeff2e97cd48a8f2909119")
                        && !"1".equals(System.getenv("NSPIRE_ALLOW_NGC_ENTRY_STAGE5_UPLOAD")))
                    throw new IllegalArgumentException("refusing NGC entry stage-5 probe without explicit upload confirmation");
                if (artifactSha.equals("0bf950738fcd16e69fe97163cbcf69b6edd96057415ddd14b2a2687d522e91ed")
                        && !"1".equals(System.getenv("NSPIRE_ALLOW_NGC_ENTRY_STAGE6_UPLOAD")))
                    throw new IllegalArgumentException("refusing NGC entry stage-6 probe without explicit upload confirmation");
                if (artifactSha.equals("0d3156b7856b10e41a62578925b532972c0f1413fe053c48fb5c6149ce02cb99")
                        && !"1".equals(System.getenv("NSPIRE_ALLOW_NGC_ENTRY_STAGE7_UPLOAD")))
                    throw new IllegalArgumentException("refusing NGC entry stage-7 probe without explicit upload confirmation");
                if (artifactSha.equals("9cdf132883b0001259aaee62725ae5b0232cf8e7c477c3b879f5cf27cfffee5a"))
                    throw new IllegalArgumentException("refusing stage-8 probe: handheld rejected it as unsupported document format on 2026-09-24");
                if (artifactSha.equals("a88bd700651bac3876a23e4b0e45428535102f1834524169219b04a249b499ef"))
                    throw new IllegalArgumentException("refusing scheduler-yield candidate: handheld rejected it as unsupported document format on 2026-09-24");
                if (artifactSha.equals("86b883f680a41f3c034167227ae3b0645d26652a8e8a299b2857b0d17a2f1ea1"))
                    throw new IllegalArgumentException("refusing size-reduced candidate: handheld rejected it as unsupported document format on 2026-09-24");
                if (artifactSha.equals("91820a3ac0ec564a995f0136e64b2c8d384ce9507ea8d410d9f99bb97fb70858"))
                    throw new IllegalArgumentException("refusing superseded NGC stage-isolation candidate; rebuild with lcd_init before upload");
                if (artifactSha.equals("34345e069a1bbaacd1b4b289e37ada9d04d8f1105c11fa259b5d519937998f26")
                        && !"1".equals(System.getenv("NSPIRE_ALLOW_STAGE_CANDIDATE_UPLOAD")))
                    throw new IllegalArgumentException("refusing NGC lcd_init stage candidate without explicit upload confirmation");
                proxy.sendFileToNode(node, source.getAbsolutePath(), args[2]);
                System.out.println("UPLOADED " + args[2] + " bytes=" + source.length());
                return;
            }
            if (command.equals("screen") && args.length == 2) {
                saveScreen(proxy, node, args[1]);
                return;
            }
            if (command.equals("key") && args.length >= 2) {
                for (String key : Arrays.copyOfRange(args, 1, args.length)) sendKey(proxy, node, key);
                return;
            }
            if (command.equals("type") && args.length == 2) {
                for (int i = 0; i < args[1].length(); i++) sendKey(proxy, node, String.valueOf(args[1].charAt(i)));
                return;
            }
            usage();
            System.exit(2);
        } catch (Exception error) {
            exitCode = 1;
            throw error;
        } finally {
            // RMI leaves non-daemon threads alive even after client shutdown.
            // This JVM is a disposable CLI; the shell preserves shared servers.
            Thread shutdown = new Thread(() -> {
                try { proxy.shutdown(); } catch (RuntimeException ignored) { }
            }, "remote-proxy-shutdown");
            shutdown.setDaemon(true);
            shutdown.start();
            shutdown.join(1500L);
            final int resultCode = exitCode;
            Thread exit = new Thread(() -> {
                try { Thread.sleep(1000L); } catch (InterruptedException ignored) { }
                Runtime.getRuntime().halt(resultCode);
            }, "remote-cli-exit");
            exit.setDaemon(false);
            exit.start();
        }
    }
}
