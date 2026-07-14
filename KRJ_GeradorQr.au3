; ==========================================================
; Gerador QR Karaokê RJ - FINAL COM LICENÇA POR HD
; Gera:
; - Pasta com o número da máquina (ex.: KRJ00022)
; - QR_EQUIPAMENTO.png mostrando o número da máquina na imagem
; - MAQUINA_KRJ.txt com UMA LINHA contendo somente o codigo da maquina
; - LICENCA_HD_KRJ.txt com UMA LINHA contendo somente a licenca/codigo do HD
;
; Ao abrir: pede senha Junior1981#
; Se errar/cancelar: fecha
; Dentro do sistema: somente colar o código do HD enviado pelo cliente
; ==========================================================

#include <GUIConstantsEx.au3>
#include <WindowsConstants.au3>
#include <StaticConstants.au3>
#include <EditConstants.au3>
#include <ButtonConstants.au3>
#include <GDIPlus.au3>
#include <WinAPI.au3>
#include <WinAPIFiles.au3>

Global Const $URL_BASE = "https://www.karaokerj.com.br/catalogo"
Global Const $PASTA_SAIDA = @ScriptDir
Global Const $SENHA_SUPORTE = "Junior1981#"

Global $gMaquina = ""
Global $gLicencaHD = ""
Global $gPlano = ""
Global $gPastaFinal = ""
Global $gArquivoFinal = ""
Global $gModoSuporte = True

; =========================
; SENHA DE ABERTURA
; =========================
Local $sSenhaAbrir = InputBox("Senha", "Digite a senha para abrir o Gerador QR:", "", "*")
If @error Then Exit

If $sSenhaAbrir <> $SENHA_SUPORTE Then
    MsgBox(16, "Acesso negado", "Senha incorreta.")
    Exit
EndIf

; =========================
; GUI
; =========================
Global $hGUI = GUICreate("Gerador QR Karaokê RJ - Liberação por HD", 620, 430)

GUICtrlCreateLabel("Número da máquina (somente pasta e imagem):", 20, 22, 280, 22)
Global $inpMaquina = GUICtrlCreateInput("", 20, 48, 180, 28)
GUICtrlSetData($inpMaquina, "KRJ")

GUICtrlCreateLabel("Código do HD enviado pelo cliente:", 20, 95, 300, 22)
Global $inpLicenca = GUICtrlCreateInput("", 20, 122, 360, 28)

GUICtrlCreateLabel("Catálogo:", 20, 175, 120, 22)
Global $cmbPlano = GUICtrlCreateCombo("PLUS", 20, 200, 160, 28)
GUICtrlSetData($cmbPlano, "BASICO|PLUS", "PLUS")

Global $btnGerar = GUICtrlCreateButton("GERAR QR CODE", 400, 48, 180, 36)

GUICtrlCreateLabel("Como vai ficar o link:", 20, 250, 180, 22)
Global $lblPreview = GUICtrlCreateLabel("", 20, 275, 580, 45)
Global $lblStatus = GUICtrlCreateLabel("", 20, 330, 580, 40)

Global $btnAbrir = GUICtrlCreateButton("ABRIR PASTA", 20, 382, 120, 35)
Global $btnImagem = GUICtrlCreateButton("ABRIR IMAGEM", 155, 382, 120, 35)
Global $btnSair = GUICtrlCreateButton("SAIR", 490, 382, 90, 35)

GUICtrlSetState($btnAbrir, $GUI_DISABLE)
GUICtrlSetState($btnImagem, $GUI_DISABLE)

GUISetState(@SW_SHOW)

While True
    _AtualizarPreview()
    Sleep(40)
    Switch GUIGetMsg()
        Case $GUI_EVENT_CLOSE, $btnSair
            Exit

        Case $btnGerar
            _Gerar()


        Case $btnAbrir
            If $gPastaFinal <> "" And FileExists($gPastaFinal) Then ShellExecute($gPastaFinal)

        Case $btnImagem
            If $gArquivoFinal <> "" And FileExists($gArquivoFinal) Then ShellExecute($gArquivoFinal)
    EndSwitch
WEnd

; =========================
; SENHA JÁ VALIDADA NA ABERTURA
; =========================
Func _ModoSuporte()
    ; Mantido apenas por compatibilidade.
    Return
EndFunc

Func _UsarHDLocal()
    ; Nesta versão o gerador não usa o HD local.
    ; Cole o código enviado pelo cliente.
    Return
EndFunc

; =========================
; PREVIEW / URL
; =========================
Func _AtualizarPreview()
    Local Static $last = ""

    Local $maquina = _NormalizarEquipamento(GUICtrlRead($inpMaquina))
    Local $plano = _NormalizarPlano(GUICtrlRead($cmbPlano))

    If $plano = "" Then
        GUICtrlSetData($lblPreview, "Escolha PLUS ou BASICO.")
        Return
    EndIf

    Local $url = _MontarUrlCatalogo($maquina, $plano)

    If $url <> $last Then
        $last = $url
        GUICtrlSetData($lblPreview, _TextoParaTela($url))
    EndIf
EndFunc

Func _MontarUrlCatalogo($maquina, $plano)
    If $maquina = "" Then Return $URL_BASE & "?plano=" & StringLower($plano)
    Return $URL_BASE & "?m=" & _UrlEncode($maquina) & "&plano=" & StringLower($plano)
EndFunc

Func _TextoParaTela($txt)
    Return StringReplace($txt, "&", "&&")
EndFunc

; =========================
; GERAR
; =========================
Func _Gerar()
    Local $maquina = _NormalizarEquipamento(GUICtrlRead($inpMaquina))
    Local $licenca = _NormalizarCodigoHD(GUICtrlRead($inpLicenca))
    Local $plano = _NormalizarPlano(GUICtrlRead($cmbPlano))

    GUICtrlSetData($inpMaquina, $maquina)
    GUICtrlSetData($inpLicenca, $licenca)
    GUICtrlSetData($cmbPlano, $plano)

    If $maquina = "" Or StringLeft($maquina, 3) <> "KRJ" Then
        MsgBox(48, "Atenção", "Informe o número da máquina para organizar a pasta e aparecer na imagem." & @CRLF & "Exemplo: KRJ00022")
        Return
    EndIf

    If $licenca = "" Or StringLeft($licenca, 5) <> "KRJHD" Then
        MsgBox(48, "Atenção", "Código do HD inválido." & @CRLF & "Cole o código gerado pelo monitor no arquivo CODIGO_HD_PARA_LIBERAR.txt.")
        Return
    EndIf

    If $plano = "" Then
        MsgBox(48, "Atenção", "Escolha o catálogo: PLUS ou BASICO.")
        Return
    EndIf

    Local $urlQR = _MontarUrlCatalogo($maquina, $plano)

    Local $resp = MsgBox(36, "Confirmar QR Code", _
        "Pasta/imagem da máquina: " & $maquina & @CRLF & _
        "Código da máquina que irá no QR/MAQUINA_KRJ.txt: " & $maquina & @CRLF & _
        "Licença HD que irá somente no LICENCA_HD_KRJ.txt: " & $licenca & @CRLF & @CRLF & _
        "Link do QR Code:" & @CRLF & $urlQR & @CRLF & @CRLF & _
        "Deseja confirmar e gerar?")

    If $resp <> 6 Then
        GUICtrlSetData($lblStatus, "Operação cancelada.")
        Return
    EndIf

    $gMaquina = $maquina
    $gLicencaHD = $licenca
    $gPlano = $plano
    $gPastaFinal = $PASTA_SAIDA & "\" & $maquina
    $gArquivoFinal = $gPastaFinal & "\QR_" & $maquina & "_" & $plano & ".png"

    DirCreate($gPastaFinal)

    ; TXT separado:
    ; MAQUINA_KRJ.txt = somente codigo da maquina para QR/servidor
    ; LICENCA_HD_KRJ.txt = somente codigo do HD para validar localmente no monitor
    Local $txt = $gPastaFinal & "\MAQUINA_KRJ.txt"
    Local $txtLicenca = $gPastaFinal & "\LICENCA_HD_KRJ.txt"

    FileDelete($txt)
    FileWrite($txt, $maquina)

    FileDelete($txtLicenca)
    FileWrite($txtLicenca, $licenca)

    Local $qrTemp = $gPastaFinal & "\QR_TEMP.png"
    FileDelete($qrTemp)

    Local $api = "https://api.qrserver.com/v1/create-qr-code/?size=520x520&data=" & _UrlEncode($urlQR)
    GUICtrlSetData($lblStatus, "Gerando QR Code..." & @CRLF & _TextoParaTela($urlQR))

    InetGet($api, $qrTemp, 1, 0)

    If Not FileExists($qrTemp) Then
        MsgBox(16, "Erro", "Não foi possível baixar o QR Code." & @CRLF & "Verifique a internet.")
        Return
    EndIf

    If Not _CriarArteFinal($maquina, $licenca, $plano, $qrTemp, $gArquivoFinal) Then
        MsgBox(16, "Erro", "Falha ao criar a imagem final.")
        Return
    EndIf

    FileDelete($qrTemp)

    GUICtrlSetData($lblStatus, _
        "Arquivos gerados com sucesso:" & @CRLF & _
        $gArquivoFinal & @CRLF & _
        $txt & @CRLF & _
        $txtLicenca)

    GUICtrlSetState($btnAbrir, $GUI_ENABLE)
    GUICtrlSetState($btnImagem, $GUI_ENABLE)

    MsgBox(64, "Concluído", "QR Code gerado com sucesso!")
EndFunc

; =========================
; NORMALIZA CÓDIGO DO HD
; =========================
Func _NormalizarCodigoHD($s)
    $s = StringStripWS($s, 8)
    $s = StringUpper($s)
    $s = StringRegExpReplace($s, "[^A-Z0-9]", "")
    Return $s
EndFunc

; =========================
; ARTE FINAL PNG
; =========================

Func _CriarArteFinal($maquina, $licenca, $plano, $qrPath, $outPath)

    _GDIPlus_Startup()

    Local $w = 900
    Local $h = 1100

    Local $hBitmap = _GDIPlus_BitmapCreateFromScan0($w, $h)
    Local $hGraphics = _GDIPlus_ImageGetGraphicsContext($hBitmap)

    Local $brushWhite = _GDIPlus_BrushCreateSolid(0xFFFFFFFF)
    _GDIPlus_GraphicsFillRect($hGraphics, 0, 0, $w, $h, $brushWhite)

    ; QR CODE
    Local $qr = _GDIPlus_ImageLoadFromFile($qrPath)
    If @error Or $qr = 0 Then
        _GDIPlus_GraphicsDispose($hGraphics)
        _GDIPlus_BitmapDispose($hBitmap)
        _GDIPlus_Shutdown()
        Return False
    EndIf

    Local $qrSize = 620
    Local $x = Int(($w - $qrSize) / 2)
    Local $y = 410

    _GDIPlus_GraphicsDrawImageRect($hGraphics, $qr, $x, $y, $qrSize, $qrSize)
    _GDIPlus_ImageDispose($qr)

    ; Converte para HBITMAP para desenhar texto com GDI nativo
    Local $hHBitmap = _GDIPlus_BitmapCreateHBITMAPFromBitmap($hBitmap)
    Local $hDC = _WinAPI_GetDC(0)
    Local $hMemDC = _WinAPI_CreateCompatibleDC($hDC)
    Local $hOld = _WinAPI_SelectObject($hMemDC, $hHBitmap)

    If $maquina <> "" Then
        _DrawTextCenterNative($hMemDC, "Escaneie para enviar musicas", 0, 70, $w, 70, 42, 700)
        _DrawTextCenterNative($hMemDC, "Equipamento:", 0, 190, $w, 55, 34, 700)
        _DrawTextCenterNative($hMemDC, $maquina, 0, 255, $w, 70, 50, 800)
        _DrawTextCenterNative($hMemDC, "Catalogo: " & $plano, 0, 335, $w, 50, 30, 700)
    Else
        _DrawTextCenterNative($hMemDC, "Escaneie para consultar o catalogo", 0, 90, $w, 80, 38, 700)
        _DrawTextCenterNative($hMemDC, "Karaoke RJ", 0, 205, $w, 70, 50, 800)
        _DrawTextCenterNative($hMemDC, "Catalogo: " & $plano, 0, 315, $w, 50, 32, 700)
    EndIf

    _WinAPI_SelectObject($hMemDC, $hOld)
    _WinAPI_DeleteDC($hMemDC)
    _WinAPI_ReleaseDC(0, $hDC)

    Local $hFinal = _GDIPlus_BitmapCreateFromHBITMAP($hHBitmap)

    FileDelete($outPath)
    _GDIPlus_ImageSaveToFile($hFinal, $outPath)

    _GDIPlus_BitmapDispose($hFinal)
    _WinAPI_DeleteObject($hHBitmap)

    _GDIPlus_BrushDispose($brushWhite)
    _GDIPlus_GraphicsDispose($hGraphics)
    _GDIPlus_BitmapDispose($hBitmap)
    _GDIPlus_Shutdown()

    Return FileExists($outPath)

EndFunc

Func _DrawTextCenterNative($hDC, $text, $x, $y, $w, $h, $fontSize, $weight)

    Local $hFont = _WinAPI_CreateFont( _
        $fontSize, 0, 0, 0, _
        $weight, False, False, False, _
        0, 0, 0, 0, 0, "Arial")

    Local $hOldFont = _WinAPI_SelectObject($hDC, $hFont)

    _WinAPI_SetBkMode($hDC, 1)
    _WinAPI_SetTextColor($hDC, 0x000000)

    Local $tRect = DllStructCreate("long Left;long Top;long Right;long Bottom")
    DllStructSetData($tRect, "Left", $x)
    DllStructSetData($tRect, "Top", $y)
    DllStructSetData($tRect, "Right", $x + $w)
    DllStructSetData($tRect, "Bottom", $y + $h)

    DllCall("user32.dll", "int", "DrawTextW", _
        "handle", $hDC, _
        "wstr", $text, _
        "int", -1, _
        "struct*", $tRect, _
        "uint", BitOR(0x00000001, 0x00000004, 0x00000020))

    _WinAPI_SelectObject($hDC, $hOldFont)
    _WinAPI_DeleteObject($hFont)

EndFunc

Func _DrawCenteredText($g, $text, $font, $brush, $x, $y, $w, $h, $format)
    Local $layout = _GDIPlus_RectFCreate($x, $y, $w, $h)
    _GDIPlus_GraphicsDrawStringEx($g, $text, $font, $layout, $format, $brush)
EndFunc


; =========================
; IDENTIFICAÇÃO DO HD
; =========================
Func _GerarCodigoHD()

    Local $serial = _ObterSerialHD()
    $serial = _NormalizarHardware($serial)

    If $serial = "" Then
        ; Plano B: usa serial do volume do Windows caso o serial físico não seja retornado pelo WMI.
        $serial = "VOL" & DriveGetSerial(@HomeDrive & "\")
        $serial = _NormalizarHardware($serial)
    EndIf

    If $serial = "" Then
        Return "ERROHD0000000000"
    EndIf

    ; Código mascarado. Não grava o serial real do HD.
    ; O monitor deve usar a mesma regra para validar.
    Local $h1 = _CRC32_HEX($serial & "|KARAOKERJ|2026|A")
    Local $h2 = _CRC32_HEX("B|2026|KARAOKERJ|" & $serial)

    Return "KRJHD" & $h1 & $h2

EndFunc

Func _ObterSerialHD()

    Local $objWMI = ObjGet("winmgmts:\\.\root\cimv2")
    If @error Or Not IsObj($objWMI) Then Return ""

    ; Primeiro tenta pegar o disco físico principal pelo Index 0.
    Local $colItems = $objWMI.ExecQuery("SELECT SerialNumber, Index FROM Win32_DiskDrive WHERE Index=0")
    If IsObj($colItems) Then
        For $objItem In $colItems
            Local $s = StringStripWS($objItem.SerialNumber, 3)
            If $s <> "" Then Return $s
        Next
    EndIf

    ; Se falhar, pega o primeiro serial físico disponível.
    $colItems = $objWMI.ExecQuery("SELECT SerialNumber FROM Win32_DiskDrive")
    If IsObj($colItems) Then
        For $objItem In $colItems
            Local $s = StringStripWS($objItem.SerialNumber, 3)
            If $s <> "" Then Return $s
        Next
    EndIf

    Return ""

EndFunc

Func _NormalizarHardware($s)
    $s = StringStripWS($s, 8)
    $s = StringUpper($s)
    $s = StringRegExpReplace($s, "[^A-Z0-9]", "")
    Return $s
EndFunc

Func _CRC32_HEX($s)

    Local $crc = 0xFFFFFFFF
    Local $b, $i, $j

    For $i = 1 To StringLen($s)
        $b = Asc(StringMid($s, $i, 1))
        $crc = BitXOR($crc, $b)

        For $j = 1 To 8
            If BitAND($crc, 1) Then
                $crc = BitXOR(BitShift($crc, 1), 0xEDB88320)
            Else
                $crc = BitShift($crc, 1)
            EndIf
        Next
    Next

    $crc = BitXOR($crc, 0xFFFFFFFF)
    Return StringRight("00000000" & Hex($crc, 8), 8)

EndFunc

; =========================
; NORMALIZAÇÃO
; =========================
Func _NormalizarEquipamento($s)

    $s = StringStripWS($s, 3)
    $s = StringUpper($s)

    $s = StringReplace($s, "Á", "A")
    $s = StringReplace($s, "À", "A")
    $s = StringReplace($s, "Â", "A")
    $s = StringReplace($s, "Ã", "A")
    $s = StringReplace($s, "É", "E")
    $s = StringReplace($s, "Ê", "E")
    $s = StringReplace($s, "Í", "I")
    $s = StringReplace($s, "Ó", "O")
    $s = StringReplace($s, "Ô", "O")
    $s = StringReplace($s, "Õ", "O")
    $s = StringReplace($s, "Ú", "U")
    $s = StringReplace($s, "Ç", "C")

    $s = StringRegExpReplace($s, "\s+", "-")
    $s = StringRegExpReplace($s, "[^A-Z0-9\-_]", "")

    Return $s

EndFunc


Func _NormalizarPlano($s)

    $s = StringStripWS($s, 3)
    $s = StringUpper($s)
    $s = StringReplace($s, "Á", "A")
    $s = StringReplace($s, "À", "A")
    $s = StringReplace($s, "Â", "A")

    If $s = "BASICO" Or $s = "BÁSICO" Then Return "BASICO"
    If $s = "PLUS" Then Return "PLUS"

    Return ""

EndFunc

; =========================
; URL ENCODE SIMPLES
; =========================
Func _UrlEncode($s)
    Local $out = ""
    Local $c, $a, $i

    For $i = 1 To StringLen($s)
        $c = StringMid($s, $i, 1)
        $a = Asc($c)

        If ($a >= 48 And $a <= 57) Or _
           ($a >= 65 And $a <= 90) Or _
           ($a >= 97 And $a <= 122) Or _
           $c = "-" Or $c = "_" Or $c = "." Or $c = "~" Then
            $out &= $c
        Else
            $out &= "%" & Hex($a, 2)
        EndIf
    Next

    Return $out
EndFunc