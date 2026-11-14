import java.nio.file.*;
import java.io.*;
import java.security.MessageDigest;
import java.util.*;

/** Exclusive destination reservation; COMPLETE is published only after all files. */
public class HGExportSet {
    private final Path root, stage;
    public HGExportSet(Path destination) throws IOException {
        root = destination;
        Path parent = root.toAbsolutePath().getParent();
        Files.createDirectories(parent);
        Files.createDirectory(root); // collision fails before touching prior files
        Files.createFile(root.resolve(".incomplete"));
        stage = Files.createDirectory(root.resolve(".partial"));
    }
    public Path staging() { return stage; }
    public void finish() throws Exception {
        List<Path> files;
        try (var stream = Files.list(stage)) { files = stream.sorted().toList(); }
        StringBuilder json = new StringBuilder("{\"schema\":\"hg-export-set/v1\",\"files\":{");
        boolean first = true;
        for (Path p : files) {
            String name = p.getFileName().toString();
            if (!name.matches("[A-Za-z0-9_.-]+")) throw new IOException("Unsafe output name");
            byte[] hash = MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(p));
            if (!first) json.append(','); first = false;
            json.append('"').append(name).append("\":\"").append(HexFormat.of().formatHex(hash)).append('"');
            Files.move(p, root.resolve(name));
        }
        Files.delete(stage);
        Files.writeString(root.resolve(".complete.tmp"), json.append("}}\n").toString(), StandardOpenOption.CREATE_NEW);
        Files.move(root.resolve(".complete.tmp"), root.resolve("COMPLETE.json"), StandardCopyOption.ATOMIC_MOVE);
        Files.delete(root.resolve(".incomplete"));
    }
}
