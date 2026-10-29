using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using AiReportDesktop;

var record = new Dictionary<string, object?>();
var settingsPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AIassist", "backend.json");
var previousSettings = File.Exists(settingsPath) ? File.ReadAllText(settingsPath) : null;
string? projectId = null;
try
{
    // Exercise the exact handler compiled into the desktop app, with a controlled local backend.
    using var listener = new HttpListener();
    listener.Prefixes.Add("http://127.0.0.1:18081/");
    listener.Start();
    BackendConnection.Configure(false, "http://127.0.0.1:18081/api", "http://10.60.150.179:8000/api");
    BackendConnection.Token = "local-session-test";
    var request = BackendConnection.Client.GetAsync(BackendConnection.BaseUrl + "/health");
    var context = await listener.GetContextAsync();
    record["local_bearer"] = context.Request.Headers["Authorization"] == "Bearer local-session-test";
    context.Response.StatusCode = 204;
    context.Response.Close();
    using (var response = await request) record["empty_204"] = (await response.Content.ReadAsByteArrayAsync()).Length == 0;
    var unauthorized = false;
    BackendConnection.Unauthorized += () => unauthorized = true;
    request = BackendConnection.Client.GetAsync(BackendConnection.BaseUrl + "/projects");
    context = await listener.GetContextAsync();
    context.Response.StatusCode = 401;
    context.Response.Close();
    using (await request) { }
    record["expired_token_cleared"] = unauthorized && BackendConnection.Token == null;
    try
    {
        await BackendConnection.Client.GetAsync("http://example.com/api/projects");
        record["cross_origin_blocked"] = false;
    }
    catch (InvalidOperationException) { record["cross_origin_blocked"] = true; }
    BackendConnection.Token = "must-not-survive-switch";
    BackendConnection.Configure(true, "http://127.0.0.1:18081/api", "http://10.60.150.179:8000/api");
    record["switch_clears_session"] = BackendConnection.Token == null && BackendConnection.UseServer;
    // A failed candidate probe must not displace a working business connection.
    BackendConnection.Token = "retained-current-session";
    var candidate = BackendConnection.ValidateEndpointAsync("http://127.0.0.1:18081/api", true, null);
    context = await listener.GetContextAsync();
    context.Response.StatusCode = 503;
    context.Response.Close();
    try { await candidate; record["failed_switch_keeps_connection"] = false; }
    catch (HttpRequestException)
    {
        record["failed_switch_keeps_connection"] = BackendConnection.UseServer && BackendConnection.Token == "retained-current-session";
    }
    candidate = BackendConnection.ValidateEndpointAsync("http://127.0.0.1:18081/api", true, null);
    context = await listener.GetContextAsync();
    context.Response.StatusCode = 200;
    context.Response.Close();
    context = await listener.GetContextAsync();
    record["candidate_does_not_receive_current_token"] = context.Request.Headers["Authorization"] == null;
    context.Response.StatusCode = 404;
    context.Response.Close();
    record["legacy_local_can_connect"] = await candidate;
    candidate = BackendConnection.ValidateEndpointAsync("http://127.0.0.1:18081/api", false, null);
    context = await listener.GetContextAsync();
    context.Response.StatusCode = 200;
    context.Response.Close();
    context = await listener.GetContextAsync();
    context.Response.StatusCode = 401;
    context.Response.Close();
    record["new_backend_requires_login"] = !await candidate;
    BackendConnection.Token = null;
    listener.Stop();

    async Task<JsonElement> Call(string method, string path, object? body = null)
    {
        using var msg = new HttpRequestMessage(new HttpMethod(method), BackendConnection.BaseUrl + path);
        if (body != null) msg.Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        using var response = await BackendConnection.Client.SendAsync(msg);
        response.EnsureSuccessStatusCode();
        var bytes = await response.Content.ReadAsByteArrayAsync();
        if (bytes.Length == 0) return default;
        using var doc = JsonDocument.Parse(bytes);
        return doc.RootElement.Clone();
    }

    var username = "desktop_cs_" + Convert.ToHexString(RandomNumberGenerator.GetBytes(5)).ToLowerInvariant();
    var password = Convert.ToBase64String(RandomNumberGenerator.GetBytes(24));
    var user = await Call("POST", "/auth/register", new { username, password, display_name = "C#桌面传输联调" });
    record["test_user_id"] = user.GetProperty("id").GetString();
    var login = await Call("POST", "/auth/login", new { username, password });
    BackendConnection.Token = login.GetProperty("access_token").GetString();
    var me = await Call("GET", "/auth/me");
    record["server_login_me"] = me.GetProperty("id").GetString() == user.GetProperty("id").GetString();
    var project = await Call("POST", "/projects", new { name = "C#桌面链路验证_" + username });
    projectId = project.GetProperty("id").GetString();
    var tree = await Call("GET", "/projects/tree");
    var projects = tree.ValueKind == JsonValueKind.Array ? tree : tree.GetProperty("items");
    record["server_project_tree"] = projects.EnumerateArray().Any(p => p.GetProperty("id").GetString() == projectId);
    await Call("DELETE", "/projects/" + projectId);
    projectId = null;
    record["test_project_deleted"] = true;
    await Call("POST", "/auth/logout");
    using var revoked = await BackendConnection.Client.GetAsync(BackendConnection.BaseUrl + "/auth/me");
    record["logout_revokes_session"] = revoked.StatusCode == HttpStatusCode.Unauthorized && BackendConnection.Token == null;
    record["success"] = record.Values.OfType<bool>().All(value => value);
}
finally
{
    if (BackendConnection.Token != null)
    {
        if (projectId != null)
            using (await BackendConnection.Client.DeleteAsync(BackendConnection.BaseUrl + "/projects/" + projectId)) { }
        using (await BackendConnection.Client.PostAsync(BackendConnection.BaseUrl + "/auth/logout", null)) { }
    }
    BackendConnection.Token = null;
    if (previousSettings != null) File.WriteAllText(settingsPath, previousSettings);
    else if (File.Exists(settingsPath)) File.Delete(settingsPath);
    Directory.CreateDirectory("output");
    File.WriteAllText("output/desktop-connection-smoke-20261023.json", JsonSerializer.Serialize(record, new JsonSerializerOptions { WriteIndented = true }));
}
Console.WriteLine(JsonSerializer.Serialize(record));
return record.TryGetValue("success", out var success) && success is true ? 0 : 1;
