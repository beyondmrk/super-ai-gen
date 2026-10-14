// hf.exe replacement for hf-accounts: hands every Higgsfield CLI call to router.py.
// install.py fills in the two paths below and compiles this with the .NET Framework csc.exe.
// The command line after the program name is passed on byte for byte (never re-quoted), because
// prompts span several lines.
using System;
using System.Diagnostics;
using System.IO;

static class HfStub
{
    const string Python = @"__PYTHON__";
    const string Router = @"__ROUTER__";

    static int Main()
    {
        string rest = ArgsAfterProgram(Environment.CommandLine);
        if (!File.Exists(Python) || !File.Exists(Router))
        {
            Console.Error.WriteLine("HF ROUTER: refused. The account router is missing (" + Python +
                                    " / " + Router + "). Run the hf-accounts install.");
            return 64;
        }
        var psi = new ProcessStartInfo(Python, "-E -s \"" + Router + "\" " + rest);
        psi.UseShellExecute = false;
        psi.EnvironmentVariables["HF_ROUTER_RAW"] = rest;
        psi.EnvironmentVariables["HF_ROUTER_VENDOR"] = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        using (var p = Process.Start(psi))
        {
            p.WaitForExit();
            return p.ExitCode;
        }
    }

    // The argv[0] rule Windows uses: a quoted program name runs to the next quote, otherwise to
    // the first space or tab.
    static string ArgsAfterProgram(string cl)
    {
        int i = 0;
        while (i < cl.Length && (cl[i] == ' ' || cl[i] == '\t')) i++;
        if (i < cl.Length && cl[i] == '"')
        {
            i++;
            while (i < cl.Length && cl[i] != '"') i++;
            if (i < cl.Length) i++;
        }
        else
        {
            while (i < cl.Length && cl[i] != ' ' && cl[i] != '\t') i++;
        }
        while (i < cl.Length && (cl[i] == ' ' || cl[i] == '\t')) i++;
        return cl.Substring(i);
    }
}
