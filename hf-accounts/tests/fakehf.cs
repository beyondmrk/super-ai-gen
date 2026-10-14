// Test double for the real Higgsfield binary, installed as hf.real.exe in a temp vendor folder.
// `workspace status` prints %USERPROFILE%\.config\higgsfield\fake_ws.json and `account status`
// prints fake_account.json beside it (each fails like a signed-out CLI when its file is missing);
// anything else echoes USERPROFILE and each argument, one per line.
using System;
using System.IO;
using System.Text;

static class FakeHf
{
    static int Main(string[] args)
    {
        var o = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false));
        o.AutoFlush = true;
        string profile = Environment.GetEnvironmentVariable("USERPROFILE") ?? "";
        string file = null;
        if (args.Length >= 2 && args[1] == "status")
        {
            if (args[0] == "workspace") file = "fake_ws.json";
            if (args[0] == "account") file = "fake_account.json";
        }
        if (file != null)
        {
            string f = Path.Combine(profile, ".config", "higgsfield", file);
            if (!File.Exists(f)) { Console.Error.WriteLine("Error: not logged in"); return 1; }
            o.Write(File.ReadAllText(f));
            return 0;
        }
        o.WriteLine("FAKEHF USERPROFILE=" + profile);
        for (int i = 0; i < args.Length; i++)
            o.WriteLine("ARG[" + i + "]=" + args[i].Replace("\r", "\\r").Replace("\n", "\\n"));
        return args.Length > 0 && args[0] == "fail" ? 3 : 0;
    }
}
