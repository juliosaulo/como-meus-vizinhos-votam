<?php
/**
 * Imagem da prévia de um local de votação: /card/{municipio}/{zona-local}.png
 *
 * 1200×630, o tamanho que WhatsApp, X e Facebook leem. Desenhada sob demanda e
 * guardada em cache: são 74 mil locais, e gerar todos de antemão seria jogar
 * fora quase tudo. O caminho do cache carrega a versão dos dados, então uma
 * rodada nova do pipeline invalida as imagens sem limpeza manual.
 *
 * O card identifica o local de votação. Rua, número e bairro digitados por quem
 * compartilhou não entram aqui nunca.
 *
 * Duas checagens antes de desenhar, nesta ordem: o formato de `municipio` e
 * `id`, para nenhum caminho de arquivo sair do previsto; e a existência da
 * região nos dados publicados, para o cache nunca passar do número de regiões
 * reais, por mais ids que um robô invente.
 *
 * Requer PHP 7.4 ou mais novo, com a extensão GD. Sem dependência externa.
 */

declare(strict_types=1);

const LARGURA = 1200;
const ALTURA = 630;

const FUNDO = [0x12, 0x23, 0x3f];
const CLARO = [0xff, 0xff, 0xff];
const ROTULO = [0x9f, 0xb3, 0xd1];
const BARRA_1 = [0xe8, 0xee, 0xf7];
const BARRA_2 = [0x8f, 0xa6, 0xc8];
const TRILHO = [0x1e, 0x33, 0x55];

function para_imagem_padrao(): void
{
    header('Location: /img/og.png', true, 302);
    exit;
}

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

function servir(string $arquivo, string $etag): void
{
    header('Content-Type: image/png');
    header('Cache-Control: public, max-age=86400');
    header("ETag: \"{$etag}\"");
    if (trim((string) ($_SERVER['HTTP_IF_NONE_MATCH'] ?? ''), '"') === $etag) {
        http_response_code(304);
        exit;
    }
    header('Content-Length: ' . (string) filesize($arquivo));
    readfile($arquivo);
    exit;
}

/** Apaga o cache das versões anteriores dos dados. */
function limpar_versoes_antigas(string $raiz, string $versao): void
{
    foreach ((array) glob($raiz . '/*', GLOB_ONLYDIR) as $pasta) {
        if (basename((string) $pasta) === $versao) {
            continue;
        }
        $itens = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator((string) $pasta, FilesystemIterator::SKIP_DOTS),
            RecursiveIteratorIterator::CHILD_FIRST
        );
        foreach ($itens as $item) {
            $item->isDir() ? @rmdir($item->getPathname()) : @unlink($item->getPathname());
        }
        @rmdir((string) $pasta);
    }
}

// ---------------------------------------------------------------- desenho
function largura_texto(string $texto, float $tamanho, string $fonte): int
{
    $caixa = imagettfbbox($tamanho, 0, $fonte, $texto);
    return $caixa === false ? 0 : (int) ($caixa[2] - $caixa[0]);
}

function escrever($img, string $texto, int $x, int $y, float $tamanho, string $fonte, int $cor): void
{
    imagettftext($img, $tamanho, 0, $x, $y, $cor, $fonte, $texto);
}

/** Corta o texto com reticências até caber na largura dada. */
function encurtar(string $texto, int $limite, float $tamanho, string $fonte): string
{
    if (largura_texto($texto, $tamanho, $fonte) <= $limite) {
        return $texto;
    }
    while (mb_strlen($texto) > 1 && largura_texto($texto . '…', $tamanho, $fonte) > $limite) {
        $texto = mb_substr($texto, 0, mb_strlen($texto) - 1);
    }
    return rtrim($texto) . '…';
}

/** Quebra em no máximo `$linhas` linhas; o excesso vira reticências. */
function quebrar(string $texto, int $limite, float $tamanho, string $fonte, int $linhas): array
{
    $palavras = preg_split('/\s+/u', $texto) ?: [$texto];
    $saida = [];
    $atual = '';
    foreach ($palavras as $palavra) {
        $tentativa = $atual === '' ? $palavra : "{$atual} {$palavra}";
        if (largura_texto($tentativa, $tamanho, $fonte) <= $limite || $atual === '') {
            $atual = $tentativa;
            continue;
        }
        $saida[] = $atual;
        $atual = $palavra;
        if (count($saida) === $linhas) {
            break;
        }
    }
    if (count($saida) < $linhas && $atual !== '') {
        $saida[] = $atual;
    }
    $saida = array_slice($saida, 0, $linhas);
    $ultima = count($saida) - 1;
    $sobrou = largura_texto(implode(' ', $saida), $tamanho, $fonte)
        < largura_texto($texto, $tamanho, $fonte);
    if ($sobrou) {
        $saida[$ultima] = encurtar($saida[$ultima] . ' …', $limite, $tamanho, $fonte);
    }
    return $saida;
}

function pct(float $valor): string
{
    return number_format($valor, 1, ',', '.') . '%';
}

function numero(int $valor): string
{
    return number_format($valor, 0, ',', '.');
}

/** Os dois primeiros de uma lista de agregado (município ou Brasil). */
function dois_primeiros(?array $agregado, string $ano, string $turno): array
{
    $lista = $agregado['presidente'][$ano][$turno] ?? null;
    return is_array($lista) ? array_slice($lista, 0, 2) : [];
}

function linha_comparacao(string $rotulo, array $candidatos): ?string
{
    if (count($candidatos) < 2) {
        return null;
    }
    $partes = [];
    foreach ($candidatos as $c) {
        // Nome inteiro: abreviar transforma "Luiz Inácio Lula Da Silva" em outra
        // pessoa. Se não couber, quem corta é a reticência, no fim da linha.
        $partes[] = (string) $c['nome'] . ' ' . pct((float) $c['pct']);
    }
    return $rotulo . ' ' . implode(' · ', $partes);
}

// ---------------------------------------------------------------- entrada
if (!function_exists('imagecreatetruecolor') || !function_exists('imagettftext')) {
    para_imagem_padrao();
}

$municipio = (string) ($_GET['m'] ?? '');
$id = (string) ($_GET['i'] ?? '');
if (!preg_match('/^\d{7}$/', $municipio) || !preg_match('/^\d{1,4}-\d{1,5}$/', $id)) {
    para_imagem_padrao();
}

// Só desenha se a região existir de verdade nos dados publicados. Validar o
// formato do id não basta: um robô pedindo ids bem formados ao acaso encheria
// o cache de imagens inúteis e estouraria a cota de arquivos da hospedagem.
// Com esta checagem, o cache não passa do número de regiões reais.
$dados = ler_json(__DIR__ . "/publicado/compartilhar/{$municipio}.json");
if ($dados === null) {
    para_imagem_padrao();
}
if (!isset($dados['locais'][$id])) {
    $canonico = $dados['apelidos'][$id] ?? null;
    if ($canonico === null) {
        para_imagem_padrao();  // id inexistente: nada é desenhado nem gravado
    }
    $id = (string) $canonico;
}
$local = $dados['locais'][$id];
$mun = $dados['municipio'];

$meta = ler_json(__DIR__ . '/publicado/metadados.json');
$versao = preg_replace('/[^0-9A-Za-z._-]/', '', (string) ($meta['gerado_em'] ?? 'sem-versao'));
$etag = "{$versao}-{$municipio}-{$id}";

$pasta = __DIR__ . "/cache/cards/{$versao}/{$municipio}";
$arquivo = "{$pasta}/{$id}.png";
if (is_file($arquivo)) {
    servir($arquivo, $etag);
}

// ---------------------------------------------------------------- monta
$negrito = __DIR__ . '/fontes/Inter-Bold.ttf';
$meio = __DIR__ . '/fontes/Inter-SemiBold.ttf';
$normal = __DIR__ . '/fontes/Inter-Regular.ttf';

$img = imagecreatetruecolor(LARGURA, ALTURA);
$cor = static fn(array $rgb): int => (int) imagecolorallocate($img, $rgb[0], $rgb[1], $rgb[2]);
$fundo = $cor(FUNDO);
$claro = $cor(CLARO);
$rotulo = $cor(ROTULO);
$trilho = $cor(TRILHO);
$barras = [$cor(BARRA_1), $cor(BARRA_2)];

imagefilledrectangle($img, 0, 0, LARGURA, ALTURA, $fundo);

$margem = 70;
$util = LARGURA - 2 * $margem;

// As alturas são fixas e o espaçamento entre linhas é sempre o mesmo dentro de
// cada bloco: assim nome comprido de escola não empurra o resto por cima do
// rodapé, e o card sai com o mesmo ritmo em qualquer local.
escrever($img, 'COMO VOTOU MEU LOCAL DE VOTAÇÃO', $margem, 62, 15, $meio, $rotulo);

$linhas = quebrar((string) $local['local'], $util, 32, $negrito, 2);
$y = count($linhas) === 1 ? 130 : 112;
foreach ($linhas as $linha) {
    escrever($img, $linha, $margem, $y, 32, $negrito, $claro);
    $y += 42;
}

escrever($img, "{$mun['nome']} – {$mun['uf']}", $margem, 200, 21, $normal, $rotulo);

$eleicao = 'PRESIDENTE ' . $local['ano'] . ' · ' . $local['turno'] . 'º TURNO';
escrever($img, $eleicao, $margem, 242, 15, $meio, $rotulo);

// Barras: sempre em tons neutros. Cor de partido tomaria partido, e o site
// precisa circular entre eleitores dos dois lados.
$topo = 264;
foreach (array_values((array) $local['candidatos']) as $i => $candidato) {
    $texto_pct = pct((float) $candidato['pct']);
    $largura_pct = largura_texto($texto_pct, 26, $negrito);
    escrever($img, $texto_pct, LARGURA - $margem - $largura_pct, $topo + 28, 26, $negrito, $claro);

    $nome = encurtar((string) $candidato['nome'], $util - $largura_pct - 40, 24, $meio);
    escrever($img, $nome, $margem, $topo + 28, 24, $meio, $claro);

    $y_barra = $topo + 38;
    imagefilledrectangle($img, $margem, $y_barra, $margem + $util, $y_barra + 14, $trilho);
    $largura_barra = (int) round($util * min(100.0, (float) $candidato['pct']) / 100);
    imagefilledrectangle($img, $margem, $y_barra, $margem + $largura_barra, $y_barra + 14,
                         $barras[$i] ?? $barras[1]);
    $topo += 76;
}

$ano = (string) $local['ano'];
$turno = (string) $local['turno'];
$linhas_do_pe = array_values(array_filter([
    linha_comparacao('Na cidade:',
        dois_primeiros(ler_json(__DIR__ . "/publicado/agregados/municipios/{$municipio}.json"), $ano, $turno)),
    linha_comparacao('No país:',
        dois_primeiros(ler_json(__DIR__ . '/publicado/agregados/brasil.json'), $ano, $turno)),
    empty($local['deputado']) ? null : 'Deputado federal mais votado aqui: '
        . $local['deputado']['nome']
        . ($local['deputado']['partido'] ? " ({$local['deputado']['partido']})" : '')
        . ' ' . pct((float) $local['deputado']['pct']),
]));

$y = 442;
foreach ($linhas_do_pe as $linha) {
    escrever($img, encurtar($linha, $util, 17, $normal), $margem, $y, 17, $normal, $rotulo);
    $y += 32;
}

// Rodapé
$base = ALTURA - 52;
imagefilledrectangle($img, $margem, $base - 38, $margem + $util, $base - 37, $trilho);
escrever($img, 'E o seu?', $margem, $base, 19, $negrito, $claro);
escrever($img, 'comomeusvizinhosvotam.com.br',
         $margem + largura_texto('E o seu?  ', 19, $negrito), $base, 19, $normal, $rotulo);

$direita = numero((int) $local['validos']) . ' votos · Fonte: TSE';
escrever($img, $direita, LARGURA - $margem - largura_texto($direita, 16, $normal), $base, 16,
         $normal, $rotulo);

// ---------------------------------------------------------------- entrega
// Sem permissão de escrita o card ainda sai; só não fica guardado.
if (is_dir(dirname($pasta)) || @mkdir(dirname($pasta), 0755, true)) {
    limpar_versoes_antigas(__DIR__ . '/cache/cards', $versao);
}
if (@mkdir($pasta, 0755, true) || is_dir($pasta)) {
    @imagepng($img, $arquivo);
}
if (is_file($arquivo)) {
    imagedestroy($img);
    servir($arquivo, $etag);
}

header('Content-Type: image/png');
header('Cache-Control: public, max-age=3600');
imagepng($img);
imagedestroy($img);
