using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using AiReportDesktop;

// Manual UI smoke host. Credentials are read from an operator-supplied private
// test fixture, authenticated over HTTP, and never entered into or exposed by UI automation.
internal static class Program
{
    [STAThread]
    private static void Main()
    {
        var app = new Application();
        app.Startup += async (_, _) =>
        {
            try
            {
                using var settings = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(AppContext.BaseDirectory, "uisettings.json")));
                using var credentials = JsonDocument.Parse(File.ReadAllText(settings.RootElement.GetProperty("credentials_path").GetString()!));
                var fixture = credentials.RootElement;
                var server = new BackendSession(new Uri(fixture.GetProperty("server_url").GetString()!));
                var local = new BackendSession(new Uri(fixture.GetProperty("local_url").GetString()!));
                await server.LoginAsync(fixture.GetProperty("server_username").GetString()!, fixture.GetProperty("server_password").GetString()!);
                await local.LoginAsync(fixture.GetProperty("local_username").GetString()!, fixture.GetProperty("local_password").GetString()!);
                var window = new MainWindow();
                app.MainWindow = window;
                window.Show();
                await window.Dispatcher.InvokeAsync(() => { }, System.Windows.Threading.DispatcherPriority.ApplicationIdle);
                await (Task)typeof(MainWindow).GetMethod("ActivateBackendSessionAsync", BindingFlags.Instance | BindingFlags.NonPublic)!
                    .Invoke(window, new object[] { local })!;
                typeof(MainWindow).GetField("_historyServerSession", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(window, server);
                typeof(MainWindow).GetField("_historyLocalSession", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(window, local);
            }
            catch (Exception error)
            {
                MessageBox.Show(error.GetType().Name + ": " + error.Message, "UI smoke failed");
                app.Shutdown(1);
            }
        };
        app.Run();
    }
}
