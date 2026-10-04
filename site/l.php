<?php
/**
 * Página de um local de votação: /l/{municipio}/{zona-local}
 *
 * Serve o mesmo index.html do site, trocando só as meta tags — é o que o
 * WhatsApp, o X e o Facebook leem ao montar a prévia do link, já que nenhum
 * deles executa o JavaScript da página. O conteúdo em si continua sendo
 * montado no navegador, pelo mesmo caminho da busca.
 *
 * Nada do endereço digitado por quem compartilhou entra aqui: o link
 * identifica o local de votação, não a pessoa.
 *
 * Requer PHP 7.4 ou mais novo. Sem dependência externa.
 */

declare(strict_types=1);

const SITE = 'https://comomeusvizinhosvotam.com.br';

function para_home(): void
{
    header('Location: /', true, 302);
    exit;
}

/** Lê um JSON publicado, já validado o nome do arquivo pelo chamador. */
function ler_json(string $caminho): ?array
{
    if (!is_file($caminho)) {
        return null;
    }
    $conteudo = file_get_contents($caminho);
    if ($conteudo === false) {
        return null;
    }
    $dados = json_decode($conteudo, true);
    return is_array($dados) ? $dados : null;
}

function esc(string $texto): string
{
    return htmlspecialchars($texto, ENT_QUOTES, 'UTF-8');
}

function pct(float $valor): string
{
    return number_format($valor, 1, ',', '.') . '%';
}

/** Troca o conteúdo de uma meta tag já existente no index.html. */
function trocar_meta(string $html, string $atributo, string $nome, string $valor): string
{
    $padrao = '/(<meta\s+' . $atributo . '="' . preg_quote($nome, '/') . '"\s+content=")[^"]*(")/i';
    return preg_replace_callback(
        $padrao,
        static fn(array $m): string => $m[1] . esc($valor) . $m[2],
        $html,
        1
    ) ?? $html;
}

// ---------------------------------------------------------------- entrada
// Validar antes de tocar em caminho de arquivo: sem isto, "../" no parâmetro
// abriria qualquer arquivo do servidor.
$municipio = (string) ($_GET['m'] ?? '');
$id = (string) ($_GET['i'] ?? '');
if (!preg_match('/^\d{7}$/', $municipio) || !preg_match('/^\d{1,4}-\d{1,5}$/', $id)) {
    para_home();
}

$dados = ler_json(__DIR__ . "/publicado/compartilhar/{$municipio}.json");
if ($dados === null || !isset($dados['locais'], $dados['municipio'])) {
    para_home();
}

// Locais que dividem o mesmo prédio compartilham a região: qualquer um deles
// leva ao endereço canônico, para não haver dois links do mesmo lugar.
if (!isset($dados['locais'][$id])) {
    $canonico = $dados['apelidos'][$id] ?? null;
    if ($canonico === null) {
        para_home();
    }
    header("Location: /l/{$municipio}/{$canonico}", true, 301);
    exit;
}

$local = $dados['locais'][$id];
$mun = $dados['municipio'];

// ---------------------------------------------------------------- textos
$titulo = "{$local['local']} — {$mun['nome']} ({$mun['uf']})";

$candidatos = $local['candidatos'] ?? [];
$placar = [];
foreach ($candidatos as $c) {
    $placar[] = $c['nome'] . ' ' . pct((float) $c['pct']);
}
// Local novo na malha não tem eleição anterior: só existe eleição a citar se
// houver placar.
$eleicao = $placar ? "Presidente {$local['ano']}, {$local['turno']}º turno" : '';

// Durante a apuração, a prévia do link não pode mostrar a eleição passada.
// O resultado ao vivo é buscado no navegador, direto no TSE: o servidor não o
// tem, e `publicado/` ainda traz o ano anterior. Mostrar aquele placar ao lado
// de um texto que fala da eleição de agora confundiria quem recebe o link — e
// o cartão é o que a maioria vê. Então, enquanto a camada ao vivo estiver
// ligada e o ano dela não estiver publicado, a prévia fica neutra: a imagem
// genérica do site, sem número, e o convite para abrir.
$ao_vivo = ler_json(__DIR__ . '/publicado/ao_vivo/config.json');
$apurando = is_array($ao_vivo)
    && !empty($ao_vivo['ativo'])
    && (string) ($ao_vivo['ano'] ?? '') !== (string) ($local['ano'] ?? '');

// Sem placar, não há card: o de um local novo na malha sairia vazio. A prévia
// fica igual à da apuração — imagem genérica e convite para abrir.
$url = SITE . "/l/{$municipio}/{$id}";
if ($apurando || !$placar) {
    $descricao = $apurando
        ? 'Apuração de ' . (string) $ao_vivo['ano'] . ' em andamento. Abra para ver '
          . 'como está votando o local de votação mais próximo do seu endereço.'
        : 'Veja como votou o local de votação mais próximo do seu endereço.';
    $card = SITE . '/img/og.png';
    $alt = "Como meus vizinhos votam — {$local['local']}, {$mun['nome']} ({$mun['uf']}).";
} else {
    $descricao = "{$eleicao}: " . implode(' × ', $placar) .
        '. Veja como votou o local de votação mais próximo do seu endereço.';
    $card = SITE . "/card/{$municipio}/{$id}.png";
    $alt = "Resultado de {$eleicao} em {$local['local']}, {$mun['nome']} ({$mun['uf']}).";
}

// ---------------------------------------------------------------- página
$html = file_get_contents(__DIR__ . '/index.html');
if ($html === false) {
    para_home();
}

// A página mora três níveis abaixo da raiz; sem <base>, todo caminho relativo
// do index.html (css/, js/, img/) apontaria para /l/{municipio}/.
$html = preg_replace('/<head>/i', "<head>\n" . '<base href="/">', $html, 1) ?? $html;

$html = preg_replace_callback(
    '/<title>.*?<\/title>/is',
    static fn(): string => '<title>' . esc($titulo) . ' | Como meus vizinhos votam?</title>',
    $html,
    1
) ?? $html;

$html = preg_replace_callback(
    '/(<link\s+rel="canonical"\s+href=")[^"]*(")/i',
    static fn(array $m): string => $m[1] . esc($url) . $m[2],
    $html,
    1
) ?? $html;

foreach ([
    ['name', 'description', $descricao],
    ['property', 'og:url', $url],
    ['property', 'og:title', $titulo],
    ['property', 'og:description', $descricao],
    ['property', 'og:image', $card],
    ['property', 'og:image:alt', $alt],
    ['name', 'twitter:title', $titulo],
    ['name', 'twitter:description', $descricao],
    ['name', 'twitter:image', $card],
    ['name', 'twitter:image:alt', $alt],
] as [$atributo, $nome, $valor]) {
    $html = trocar_meta($html, $atributo, $nome, $valor);
}

header('Content-Type: text/html; charset=utf-8');
// Mesma regra do index.html: revalida sempre. Guardar esta página por uma hora
// deixava quem reabrisse um link com a versão anterior do site — rodapé velho,
// script velho — sem jeito de perceber. Ela é pequena e montada na hora.
header('Cache-Control: no-cache');
echo $html;
