#define COBJMACROS
#include <windows.h>
#include <oledb.h>
#include <msdasc.h>
#include <stdio.h>
#include <stdlib.h>

static void log_hresult(const char *stage, HRESULT hr) {
    char line[256];
    int n = wsprintfA(line, "%s: 0x%08lx\r\n", stage, (unsigned long)hr);
    DWORD written;
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), line, n, &written, NULL);
    HANDLE log = CreateFileW(L"Z:\\tmp\\rh-probe-result.txt", FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (log != INVALID_HANDLE_VALUE) {
        WriteFile(log, line, n, &written, NULL);
        FlushFileBuffers(log); CloseHandle(log);
    }
}

static void log_com_error(void) {
    IErrorInfo *error = NULL;
    if (GetErrorInfo(0, &error) == S_OK && error) {
        BSTR description = NULL;
        if (SUCCEEDED(IErrorInfo_GetDescription(error, &description)) && description) {
            char text[2048];
            int n = WideCharToMultiByte(CP_UTF8, 0, description, -1, text, sizeof(text)-3, NULL, NULL);
            if (n > 0) {
                text[n-1]='\r'; text[n]='\n';
                DWORD written;
                WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), text, n+1, &written, NULL);
                HANDLE log = CreateFileW(L"Z:\\tmp\\rh-probe-result.txt", FILE_APPEND_DATA, FILE_SHARE_READ|FILE_SHARE_WRITE, NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
                if (log != INVALID_HANDLE_VALUE) { WriteFile(log, text, n+1, &written, NULL); CloseHandle(log); }
            }
            SysFreeString(description);
        }
        IErrorInfo_Release(error);
    }
}

static HRESULT invoke(IDispatch *obj, wchar_t *name, WORD flags, VARIANT *args, UINT count, VARIANT *result) {
    DISPID id, property = DISPID_PROPERTYPUT;
    HRESULT hr = IDispatch_GetIDsOfNames(obj, &IID_NULL, &name, 1, LOCALE_USER_DEFAULT, &id);
    if (FAILED(hr)) return hr;
    DISPPARAMS params = {args, flags == DISPATCH_PROPERTYPUT ? &property : NULL, count, flags == DISPATCH_PROPERTYPUT ? 1 : 0};
    EXCEPINFO exception = {0}; UINT arg_error = 0;
    hr = IDispatch_Invoke(obj, id, &IID_NULL, LOCALE_USER_DEFAULT, flags, &params, result, &exception, &arg_error);
    if (exception.bstrSource) SysFreeString(exception.bstrSource);
    if (exception.bstrDescription) SysFreeString(exception.bstrDescription);
    if (exception.bstrHelpFile) SysFreeString(exception.bstrHelpFile);
    return hr;
}

static HRESULT probe_ado(wchar_t *connection_string) {
    CLSID cls; IDispatch *connection=NULL, *command=NULL, *parameters=NULL, *parameter=NULL;
    VARIANT arg, result, args[3], affected; HRESULT hr;
    VariantInit(&arg); VariantInit(&result); VariantInit(&affected);
    hr=CLSIDFromProgID(L"ADODB.Connection", &cls);
    if (SUCCEEDED(hr)) hr=CoCreateInstance(&cls,NULL,CLSCTX_INPROC_SERVER,&IID_IDispatch,(void **)&connection);
    log_hresult("ADO connection object",hr); if (FAILED(hr)) goto done;
    V_VT(&arg)=VT_BSTR; V_BSTR(&arg)=SysAllocString(connection_string);
    hr=invoke(connection,L"Open",DISPATCH_METHOD,&arg,1,&result); VariantClear(&arg); VariantClear(&result);
    log_hresult("ADO open",hr); if (FAILED(hr)) goto done;
    hr=CLSIDFromProgID(L"ADODB.Command",&cls);
    if (SUCCEEDED(hr)) hr=CoCreateInstance(&cls,NULL,CLSCTX_INPROC_SERVER,&IID_IDispatch,(void **)&command);
    log_hresult("ADO command object",hr); if (FAILED(hr)) goto done;
    V_VT(&arg)=VT_DISPATCH; V_DISPATCH(&arg)=connection;
    hr=invoke(command,L"ActiveConnection",DISPATCH_PROPERTYPUT,&arg,1,NULL);
    VariantInit(&arg); log_hresult("ADO command connection",hr); if (FAILED(hr)) goto done;
    V_VT(&arg)=VT_I4; V_I4(&arg)=4;
    hr=invoke(command,L"CommandType",DISPATCH_PROPERTYPUT,&arg,1,NULL);
    log_hresult("ADO stored procedure type",hr); if (FAILED(hr)) goto done;
    V_VT(&arg)=VT_BSTR; V_BSTR(&arg)=SysAllocString(L"dbo.up_read_agentmanager");
    hr=invoke(command,L"CommandText",DISPATCH_PROPERTYPUT,&arg,1,NULL); VariantClear(&arg);
    log_hresult("ADO procedure name",hr); if (FAILED(hr)) goto done;
    hr=invoke(command,L"Parameters",DISPATCH_PROPERTYGET,NULL,0,&result);
    log_hresult("ADO parameters",hr); if (FAILED(hr)) goto done;
    parameters=V_DISPATCH(&result); VariantInit(&result);
    hr=invoke(parameters,L"Refresh",DISPATCH_METHOD,NULL,0,NULL);
    log_hresult("ADO parameter metadata",hr); if (FAILED(hr)) goto done;
    V_VT(&arg)=VT_I4; V_I4(&arg)=1;
    hr=invoke(parameters,L"Item",DISPATCH_PROPERTYGET,&arg,1,&result);
    log_hresult("ADO parameter lookup",hr); if (FAILED(hr)) goto done;
    parameter=V_DISPATCH(&result); VariantInit(&result);
    V_VT(&arg)=VT_I4; V_I4(&arg)=900;
    hr=invoke(parameter,L"Value",DISPATCH_PROPERTYPUT,&arg,1,NULL);
    log_hresult("ADO parameter value",hr); if (FAILED(hr)) goto done;
    for (int i=0;i<3;++i) VariantInit(&args[i]);
    V_VT(&args[0])=VT_I4; V_I4(&args[0])=4;
    V_VT(&args[1])=VT_ERROR; V_ERROR(&args[1])=DISP_E_PARAMNOTFOUND;
    V_VT(&args[2])=VT_BYREF|VT_VARIANT; V_VARIANTREF(&args[2])=&affected;
    hr=invoke(command,L"Execute",DISPATCH_METHOD,args,3,&result);
    log_hresult("ADO game-manager procedure execution",hr);
done:
    if (FAILED(hr)) log_com_error();
    VariantClear(&arg); VariantClear(&result); VariantClear(&affected);
    if (parameter) IDispatch_Release(parameter);
    if (parameters) IDispatch_Release(parameters);
    if (command) IDispatch_Release(command);
    if (connection) { invoke(connection,L"Close",DISPATCH_METHOD,NULL,0,NULL); IDispatch_Release(connection); }
    return hr;
}

int main(void) {
    const char *password = getenv("RH_SQL_PASSWORD");
    if (!password) return 2;
    char cs[1024];
    snprintf(cs, sizeof(cs), "Provider=SQLOLEDB;Data Source=127.0.0.1,1433;Initial Catalog=RustyHearts_Auth;User ID=sa;Password=%s", password);
    wchar_t wide[1024]; MultiByteToWideChar(CP_UTF8, 0, cs, -1, wide, 1024);
    HRESULT hr = CoInitializeEx(NULL, COINIT_MULTITHREADED);
    log_hresult("COM initialization", hr);
    if (FAILED(hr)) return 3;
    IDataInitialize *init = NULL;
    hr = CoCreateInstance(&CLSID_MSDAINITIALIZE, NULL, CLSCTX_INPROC_SERVER, &IID_IDataInitialize, (void **)&init);
    log_hresult("OLE DB initializer", hr);
    if (FAILED(hr)) { printf("OLE DB initializer unavailable: 0x%08lx\n", (unsigned long)hr); return 4; }
    IDBInitialize *db = NULL;
    hr = IDataInitialize_GetDataSource(init, NULL, CLSCTX_INPROC_SERVER, wide, &IID_IDBInitialize, (IUnknown **)&db);
    log_hresult("SQL data source", hr);
    if (SUCCEEDED(hr)) hr = IDBInitialize_Initialize(db);
    if (FAILED(hr)) log_com_error();
    log_hresult("SQL connection", hr);
    if (FAILED(hr)) printf("SQL provider connection failed: 0x%08lx\n", (unsigned long)hr);
    else { puts("SQL provider connection succeeded."); IDBInitialize_Uninitialize(db); }
    if (db) IDBInitialize_Release(db);
    IDataInitialize_Release(init);
    if (SUCCEEDED(hr)) hr=probe_ado(wide);
    CoUninitialize();
    return FAILED(hr) ? 5 : 0;
}
