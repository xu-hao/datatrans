package datatrans

import java.util.concurrent.atomic.AtomicInteger

import org.apache.hadoop.conf.Configuration
import org.apache.hadoop.fs.{ FileSystem, Path, PathFilter }
import org.apache.spark.sql.SparkSession
import scala.collection.mutable.ListBuffer
import scopt._
import java.util.Base64
import java.nio.charset.StandardCharsets
import datatrans.Config._
import net.jcazevedo.moultingyaml._
import org.joda.time._
import org.joda.time.format.{DateTimeFormat, ISODateTimeFormat}
import scala.collection.mutable.{Set, Queue}
import scala.util.control._
import Breaks._
import java.io.{StringWriter, PrintWriter}
import org.apache.log4j.{Logger, Level}
import io.circe._, io.circe.generic.semiauto._, io.circe.parser._, io.circe.syntax._

object PreprocPipeline {

  import Config._

  import Utils._

  import StepYamlProtocol._

  case class PreprocPipelineConfig(
    progress_output : String,
    report_output: String,
    steps : Seq[Step]
  )

  implicit val preprocPipelineYamlFormat = yamlFormat3(PreprocPipelineConfig)

  case class Report(
    success: Set[String],
    skip: Set[String],
    failure: Set[(String, String)],
    notRun: Set[String],
    running: Option[String],
    queued: Seq[String]
  )

  implicit val reportDecoder: Decoder[Report] = deriveDecoder
  implicit val reportEncoder: Encoder[Report] = deriveEncoder

  val log = Logger.getLogger(getClass.getName)

  log.setLevel(Level.INFO)

  def safely[T](handler: PartialFunction[Throwable, T]): PartialFunction[Throwable, T] = {
    case ex: ControlThrowable => throw ex
      // case ex: OutOfMemoryError (Assorted other nasty exceptions you don't want to catch)
	
    //If it's an exception they handle, pass it on
    case ex: Throwable if handler.isDefinedAt(ex) => handler(ex)
	
    // If they didn't handle it, rethrow. This line isn't necessary, just for clarity
    case ex: Throwable => throw ex
  }

  def main(args: Array[String]) {    

    val spark = SparkSession.builder().appName("datatrans preproc").getOrCreate()

    spark.sparkContext.setLogLevel("WARN")

    // For implicit conversions like converting RDDs to DataFrames
    // import spark.implicits._

    parseInput[PreprocPipelineConfig](args) match {
      case Some(config) =>
        val steps = config.steps
        val hc = spark.sparkContext.hadoopConfiguration
        val queued = Queue[Step]()
        val success = Set[String]()
        val failure = Set[(String, Throwable)]()
        val notRun = Set[String]()
        val skip = Set[String]()

        queued.enqueue(steps:_*)
        breakable {
          while(!queued.isEmpty) {
            breakable {
              while (true) {
                queued.dequeueFirst(step => !(step.dependsOn.toSet & (failure.map(_._1) | notRun)).isEmpty) match {
                  case None => break
                  case Some(step) =>
                    notRun.add(step.name)
                    log.info("not run: " + step.name)
                }
              }
            }

            queued.dequeueFirst(step => step.dependsOn.toSet.subsetOf(success | skip)) match {
              case None => break
              case Some(step) =>

                log.info(step)
                if(step.skip) {
                  log.info("skipped: " + step.name)
                  skip.add(step.name)
                } else {
                  try {
                    val report = Report(success, skip, failure.map{case (step, err) => (step, err.toString)}, notRun, Some(step.name), queued.map(_.name))
                    writeToFile(hc, config.progress_output, report.asJson.noSpaces)
                    val stepConfigConfig = stepConfigConfigMap(step.step.getClass().getName())
                    stepConfigConfig.step(spark, step.step.asInstanceOf[stepConfigConfig.ConfigType])
                    log.info("success: " + step.name)
                    success.add(step.name)
                  } catch safely {
                    case e: Throwable =>
                      failure.add((step.name, e))
                      val sw = new StringWriter
                      val pw = new PrintWriter(sw)
                      e.printStackTrace(pw)
                      pw.flush()
                      log.info("failure: " + step.name + " by " + e + " at " + sw.toString)
                  }
                }
            }
          }
        }
        queued.foreach(step => notRun.add(step.name))
        def printSeq[T](title: String, success: Iterable[T], indent: String = "  ") = {
          log.info(title)
          for (s <- success) {
            s match {
              case (a, b) =>
                log.info(indent + "(")
                log.info(indent + indent + a + ",")
                log.info(indent + indent + b)
                log.info(indent + ")")
              case _ => log.info(indent + s)
            }
          }
        }
        printSeq("===success===", success)
        printSeq("===skipped===", skip)
        printSeq("===failure===", failure)
        printSeq("===not run===", notRun)
        val report = Report(success, skip, failure.map{case (step, err) => (step, err.toString)}, notRun, None, Seq())
        writeToFile(hc, config.report_output, report.asJson.noSpaces)
      case None =>

    }


    spark.stop()


  }

}
