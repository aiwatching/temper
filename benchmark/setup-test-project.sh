#!/bin/bash
# Creates a realistic Java project for benchmarking Temper
# KEY DESIGN: constraints and history are NOT in code comments
# They only exist in Temper memory. Without Temper, Claude has no way to know them.

set -e
PROJECT_DIR="${1:-/tmp/temper-benchmark}"
rm -rf "$PROJECT_DIR"
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"

echo "Creating benchmark project at $PROJECT_DIR..."

# ============================================================
# AUTH MODULE (3 files) — has hidden constraints about tokens
# ============================================================
mkdir -p src/main/java/com/acme/auth

cat > src/main/java/com/acme/auth/AuthService.java << 'JAVA'
package com.acme.auth;

import com.acme.auth.TokenManager;
import com.acme.auth.SessionStore;

public class AuthService {

    private TokenManager tokenManager;
    private SessionStore sessionStore;

    public boolean authenticate(String username, String password) {
        boolean valid = ldapCheck(username, password);
        if (valid) {
            String token = tokenManager.generate(username);
            sessionStore.store(username, token);
        }
        return valid;
    }

    public void logout(String username) {
        sessionStore.invalidate(username);
    }

    public boolean validateToken(String token) {
        return tokenManager.validate(token);
    }

    public int getSessionTimeout() {
        return 30;
    }

    private boolean ldapCheck(String username, String password) {
        return true;
    }
}
JAVA

cat > src/main/java/com/acme/auth/TokenManager.java << 'JAVA'
package com.acme.auth;

import java.security.SecureRandom;
import java.util.Base64;

public class TokenManager {

    private final SecureRandom secureRandom = new SecureRandom();

    public String generate(String username) {
        byte[] bytes = new byte[32];
        secureRandom.nextBytes(bytes);
        return Base64.getEncoder().encodeToString(bytes) + ":" + username;
    }

    public boolean validate(String token) {
        return token != null && token.contains(":");
    }

    public String refresh(String oldToken) {
        return generate(extractUsername(oldToken));
    }

    private String extractUsername(String token) {
        String[] parts = token.split(":");
        return parts.length > 1 ? parts[1] : "unknown";
    }
}
JAVA

cat > src/main/java/com/acme/auth/SessionStore.java << 'JAVA'
package com.acme.auth;

import java.util.concurrent.ConcurrentHashMap;

public class SessionStore {

    private ConcurrentHashMap<String, String> sessions = new ConcurrentHashMap<>();

    public void store(String key, String value) {
        sessions.put(key, value);
    }

    public String get(String key) {
        return sessions.get(key);
    }

    public void invalidate(String key) {
        sessions.remove(key);
    }

    public int activeCount() {
        return sessions.size();
    }
}
JAVA

# ============================================================
# USER MODULE (4 files) — has hidden constraints about DAO
# ============================================================
mkdir -p src/main/java/com/acme/user

cat > src/main/java/com/acme/user/UserController.java << 'JAVA'
package com.acme.user;

import com.acme.auth.AuthService;
import com.acme.user.UserService;

@RestController
public class UserController {

    private AuthService authService;
    private UserService userService;

    @GetMapping("/api/users")
    public List<User> listUsers(String token) {
        authService.validateToken(token);
        return userService.findAll();
    }

    @GetMapping("/api/users/{id}")
    public User getUser(Long id, String token) {
        authService.validateToken(token);
        return userService.findById(id);
    }

    @PostMapping("/api/users")
    public User createUser(UserDTO dto, String token) {
        authService.validateToken(token);
        return userService.create(dto);
    }

    @DeleteMapping("/api/users/{id}")
    public void deleteUser(Long id, String token) {
        authService.validateToken(token);
        userService.delete(id);
    }

    @PutMapping("/api/users/{id}")
    public User updateUser(Long id, UserDTO dto, String token) {
        authService.validateToken(token);
        return userService.update(id, dto);
    }
}
JAVA

cat > src/main/java/com/acme/user/UserService.java << 'JAVA'
package com.acme.user;

import com.acme.user.UserDAO;
import com.acme.notification.NotificationService;

public class UserService {

    private UserDAO userDAO;
    private NotificationService notificationService;

    public List<User> findAll() {
        return userDAO.findAll();
    }

    public User findById(Long id) {
        User user = userDAO.findById(id);
        if (user == null) {
            throw new NotFoundException("User not found: " + id);
        }
        return user;
    }

    public User create(UserDTO dto) {
        validateInput(dto);
        User user = new User(dto.getName(), dto.getEmail());
        user = userDAO.save(user);
        notificationService.sendWelcome(user.getEmail());
        return user;
    }

    public User update(Long id, UserDTO dto) {
        User user = findById(id);
        validateInput(dto);
        user.setName(dto.getName());
        user.setEmail(dto.getEmail());
        return userDAO.save(user);
    }

    public void delete(Long id) {
        User user = findById(id);
        userDAO.delete(id);
        notificationService.sendAccountDeleted(user.getEmail());
    }

    private void validateInput(UserDTO dto) {
        if (dto.getEmail() == null || !dto.getEmail().contains("@") || dto.getEmail().length() > 254) {
            throw new ValidationException("Invalid email");
        }
        if (dto.getName() == null || dto.getName().trim().isEmpty()) {
            throw new ValidationException("Name required");
        }
    }
}
JAVA

cat > src/main/java/com/acme/user/UserDAO.java << 'JAVA'
package com.acme.user;

import java.util.List;

public class UserDAO {

    public List<User> findAll() {
        return null;
    }

    public User findById(Long id) {
        return null;
    }

    public User save(User user) {
        return user;
    }

    public void delete(Long id) {
    }

    public List<User> findByEmail(String email) {
        return null;
    }

    public List<User> findByName(String name) {
        return null;
    }

    public long count() {
        return 0;
    }
}
JAVA

cat > src/main/java/com/acme/user/User.java << 'JAVA'
package com.acme.user;

public class User {
    private Long id;
    private String name;
    private String email;
    private String role;
    private long createdAt;

    public User(String name, String email) {
        this.name = name;
        this.email = email;
        this.createdAt = System.currentTimeMillis();
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }
    public String getRole() { return role; }
    public void setRole(String role) { this.role = role; }
    public long getCreatedAt() { return createdAt; }
}
JAVA

# ============================================================
# NOTIFICATION MODULE (3 files)
# ============================================================
mkdir -p src/main/java/com/acme/notification

cat > src/main/java/com/acme/notification/NotificationService.java << 'JAVA'
package com.acme.notification;

public class NotificationService {

    private EmailSender emailSender;
    private TemplateEngine templateEngine;

    public void sendWelcome(String email) {
        String body = templateEngine.render("welcome", email);
        emailSender.send(email, "Welcome!", body);
    }

    public void sendAccountDeleted(String email) {
        String body = templateEngine.render("account-deleted", email);
        emailSender.send(email, "Account Deleted", body);
    }

    public void sendPasswordReset(String email, String resetLink) {
        String body = templateEngine.render("password-reset", resetLink);
        emailSender.send(email, "Password Reset", body);
    }
}
JAVA

cat > src/main/java/com/acme/notification/EmailSender.java << 'JAVA'
package com.acme.notification;

public class EmailSender {
    public void send(String to, String subject, String body) {
    }
}
JAVA

cat > src/main/java/com/acme/notification/TemplateEngine.java << 'JAVA'
package com.acme.notification;

public class TemplateEngine {
    public String render(String templateName, String... args) {
        return "<html>" + templateName + "</html>";
    }
}
JAVA

# ============================================================
# HA MODULE (1 file) — failover logic, no hint about session issue
# ============================================================
mkdir -p src/main/java/com/acme/ha

cat > src/main/java/com/acme/ha/HAManager.java << 'JAVA'
package com.acme.ha;

import com.acme.auth.SessionStore;

public class HAManager {

    private SessionStore sessionStore;

    public void failover() {
        syncDiskState();
        preWarmSessions();
        switchPrimary();
    }

    private void preWarmSessions() {
    }

    private void syncDiskState() {
    }

    private void switchPrimary() {
    }

    public String getHealthStatus() {
        return "healthy";
    }
}
JAVA

# ============================================================
# CONFIG MODULE (1 file)
# ============================================================
mkdir -p src/main/java/com/acme/config

cat > src/main/java/com/acme/config/AppConfig.java << 'JAVA'
package com.acme.config;

public class AppConfig {

    public int getDbPoolSize() {
        return 50;
    }

    public String getDbUrl() {
        return "jdbc:postgresql://db.acme.com:5432/acme";
    }

    public int getHaNodeCount() {
        return 2;
    }

    public int getMaxConnections() {
        return 100;
    }
}
JAVA

# ============================================================
# NOISE FILES — make the project big enough that grep is noisy
# ============================================================
mkdir -p src/main/java/com/acme/report
cat > src/main/java/com/acme/report/ReportService.java << 'JAVA'
package com.acme.report;
import com.acme.user.UserDAO;
public class ReportService {
    private UserDAO userDAO;
    public String generateUserReport() { return "report"; }
    public String generateAuditReport() { return "audit"; }
    public String generateComplianceReport() { return "compliance"; }
}
JAVA

mkdir -p src/main/java/com/acme/audit
cat > src/main/java/com/acme/audit/AuditService.java << 'JAVA'
package com.acme.audit;
public class AuditService {
    public void logAction(String user, String action, String detail) {}
    public void logLogin(String user) {}
    public void logLogout(String user) {}
    public void logDataAccess(String user, String resource) {}
}
JAVA

cat > src/main/java/com/acme/audit/AuditDAO.java << 'JAVA'
package com.acme.audit;
public class AuditDAO {
    public void save(String entry) {}
    public java.util.List<String> findByUser(String user) { return null; }
    public java.util.List<String> findByAction(String action) { return null; }
    public long count() { return 0; }
}
JAVA

mkdir -p src/main/java/com/acme/cache
cat > src/main/java/com/acme/cache/CacheManager.java << 'JAVA'
package com.acme.cache;
import java.util.concurrent.ConcurrentHashMap;
public class CacheManager {
    private ConcurrentHashMap<String, Object> cache = new ConcurrentHashMap<>();
    public void put(String key, Object value) { cache.put(key, value); }
    public Object get(String key) { return cache.get(key); }
    public void invalidate(String key) { cache.remove(key); }
    public void invalidateAll() { cache.clear(); }
    public int size() { return cache.size(); }
}
JAVA

mkdir -p src/main/java/com/acme/scheduling
cat > src/main/java/com/acme/scheduling/TaskScheduler.java << 'JAVA'
package com.acme.scheduling;
public class TaskScheduler {
    public void scheduleDaily(String taskName, Runnable task) {}
    public void scheduleHourly(String taskName, Runnable task) {}
    public void cancel(String taskName) {}
}
JAVA

mkdir -p src/main/java/com/acme/health
cat > src/main/java/com/acme/health/HealthCheckService.java << 'JAVA'
package com.acme.health;
import com.acme.ha.HAManager;
import com.acme.config.AppConfig;
public class HealthCheckService {
    private HAManager haManager;
    private AppConfig appConfig;
    public String checkAll() { return "ok"; }
    public String checkDatabase() { return "ok"; }
    public String checkHA() { return haManager.getHealthStatus(); }
}
JAVA

mkdir -p src/main/java/com/acme/migration
cat > src/main/java/com/acme/migration/MigrationRunner.java << 'JAVA'
package com.acme.migration;
public class MigrationRunner {
    public void runAll() {}
    public void runMigration(String name) {}
    public void rollback(String name) {}
    public java.util.List<String> getPending() { return null; }
}
JAVA

cat > src/main/java/com/acme/migration/SchemaValidator.java << 'JAVA'
package com.acme.migration;
public class SchemaValidator {
    public boolean validate() { return true; }
    public java.util.List<String> getErrors() { return null; }
}
JAVA

mkdir -p src/main/java/com/acme/integration
cat > src/main/java/com/acme/integration/LdapClient.java << 'JAVA'
package com.acme.integration;
public class LdapClient {
    public boolean authenticate(String dn, String password) { return true; }
    public java.util.Map<String, String> getUserAttributes(String dn) { return null; }
    public java.util.List<String> searchUsers(String filter) { return null; }
}
JAVA

cat > src/main/java/com/acme/integration/SmtpClient.java << 'JAVA'
package com.acme.integration;
public class SmtpClient {
    public void sendEmail(String to, String subject, String body) {}
    public boolean testConnection() { return true; }
}
JAVA

mkdir -p src/main/java/com/acme/util
cat > src/main/java/com/acme/util/StringHelper.java << 'JAVA'
package com.acme.util;
public class StringHelper {
    public static boolean isBlank(String s) { return s == null || s.trim().isEmpty(); }
    public static String truncate(String s, int max) { return s.length() > max ? s.substring(0, max) : s; }
    public static String sanitize(String s) { return s.replaceAll("[<>\"']", ""); }
}
JAVA

cat > src/main/java/com/acme/util/DateHelper.java << 'JAVA'
package com.acme.util;
public class DateHelper {
    public static long now() { return System.currentTimeMillis(); }
    public static String format(long timestamp) { return String.valueOf(timestamp); }
}
JAVA

cat > src/main/java/com/acme/util/CryptoHelper.java << 'JAVA'
package com.acme.util;
import java.security.SecureRandom;
public class CryptoHelper {
    private static final SecureRandom random = new SecureRandom();
    public static String generateSalt() { byte[] b = new byte[16]; random.nextBytes(b); return java.util.Base64.getEncoder().encodeToString(b); }
    public static String hash(String input, String salt) { return input + salt; }
}
JAVA

# --- Init git ---
git init
git add .
git commit -m "initial commit: enterprise Java app"

JAVA_COUNT=$(find src -name '*.java' | wc -l | tr -d ' ')
echo ""
echo "Test project created at $PROJECT_DIR"
echo "Files: $JAVA_COUNT Java files"
echo ""
echo "KEY: No constraints or history in code comments."
echo "     Constraints only exist in Temper memory."
